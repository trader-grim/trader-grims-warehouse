"""Identity-bound task context retrieval for continual harnesses.

This module is deliberately storage-neutral.  A caller supplies a frozen Todo
projection and persists the returned ``evidence`` envelope in its continual
harness ledger.  Context MCP may project these results, but never becomes Todo
authority or a dispatch gate.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

OUTCOMES = {
    "CURRENT", "STALE", "MISMATCHED", "ABSENT", "OPEN_BUT_IRRELEVANT", "TRUNCATED",
}
BOUNDED_ROLES = {
    "implementation", "troubleshooting", "remediation", "review", "doctor",
    "operator-assistant",
}
INVENTORY_PURPOSES = {"administrative-inventory", "planning-inventory"}


class BoundedContextError(ValueError):
    """The requested retrieval would broaden or ambiguously bind context."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _todo_id(record: Mapping[str, Any]) -> int | None:
    value = record.get("todo_id", record.get("id", record.get("todo")))
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _cursor(offset: int, generation: str, purpose: str) -> str:
    payload = {"offset": offset, "generation": generation, "purpose": purpose}
    raw = _canonical({**payload, "hash": _hash(payload)})
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str, generation: str, purpose: str) -> int:
    if not value:
        return 0
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw)
        claimed = payload.pop("hash")
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise BoundedContextError("inventory cursor is invalid") from exc
    if claimed != _hash(payload) or payload.get("generation") != generation or payload.get("purpose") != purpose:
        raise BoundedContextError("inventory cursor is stale or mismatched")
    offset = payload.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise BoundedContextError("inventory cursor offset is invalid")
    return offset


@dataclass
class BoundedContextService:
    todos: Sequence[Mapping[str, Any]]
    source_generation: str
    evidence_head: str
    current_task_id: int | None = None
    evidence_sink: Callable[[Mapping[str, Any]], None] | None = None

    def _emit(self, operation: str, scope: Mapping[str, Any], arguments: Mapping[str, Any],
              result: Mapping[str, Any]) -> dict[str, Any]:
        event = {
            "schema": "tgw-context-retrieval-evidence/v1",
            "operation": operation,
            "scope": dict(scope),
            "source_generation": self.source_generation,
            "evidence_head": self.evidence_head,
            "arguments": dict(arguments),
            "result_hash": _hash(result),
            "freshness": result.get("outcome"),
            "truncation": dict(result.get("truncation", {"truncated": False, "omitted": 0})),
        }
        event["event_hash"] = _hash(event)
        if self.evidence_sink is not None:
            self.evidence_sink(event)
        return event

    def _finish(self, operation: str, scope: Mapping[str, Any], arguments: Mapping[str, Any],
                result: dict[str, Any]) -> dict[str, Any]:
        result["evidence"] = self._emit(operation, scope, arguments, result)
        return result

    def exact(self, todo_id: int, *, role: str, expected_generation: str = "",
              expected_evidence_head: str = "") -> dict[str, Any]:
        if not isinstance(todo_id, int) or isinstance(todo_id, bool) or todo_id < 1:
            raise BoundedContextError("an exact positive Todo ID is required")
        if role not in BOUNDED_ROLES and role not in INVENTORY_PURPOSES:
            raise BoundedContextError("a declared retrieval role/capability is required")
        args = {"todo_id": todo_id, "role": role, "expected_generation": expected_generation,
                "expected_evidence_head": expected_evidence_head}
        matches = [dict(item) for item in self.todos if _todo_id(item) == todo_id]
        if not matches:
            result = {"outcome": "ABSENT", "todo": None, "truncation": {"truncated": False, "omitted": 0}}
        elif len(matches) != 1:
            result = {"outcome": "MISMATCHED", "todo": None, "reason": "duplicate Todo identity",
                      "truncation": {"truncated": False, "omitted": len(matches)}}
        elif expected_generation and expected_generation != self.source_generation:
            result = {"outcome": "STALE", "todo": None, "reason": "source generation differs",
                      "truncation": {"truncated": False, "omitted": 1}}
        elif expected_evidence_head and expected_evidence_head != self.evidence_head:
            result = {"outcome": "MISMATCHED", "todo": None, "reason": "evidence head differs",
                      "truncation": {"truncated": False, "omitted": 1}}
        elif self.current_task_id is not None and todo_id != self.current_task_id and role in BOUNDED_ROLES:
            result = {"outcome": "OPEN_BUT_IRRELEVANT", "todo": None,
                      "truncation": {"truncated": False, "omitted": 1}}
        else:
            result = {"outcome": "CURRENT", "todo": matches[0],
                      "truncation": {"truncated": False, "omitted": 0}}
        return self._finish("todo-exact", {"kind": "todo", "todo_id": todo_id, "role": role}, args, result)

    def current(self, *, role: str, expected_generation: str = "",
                expected_evidence_head: str = "") -> dict[str, Any]:
        """Resolve only the harness-bound current-task identity."""
        if self.current_task_id is None:
            args = {"role": role, "expected_generation": expected_generation,
                    "expected_evidence_head": expected_evidence_head}
            result = {"outcome": "ABSENT", "todo": None,
                      "truncation": {"truncated": False, "omitted": 0}}
            return self._finish("todo-current", {"kind": "current-task", "role": role}, args, result)
        return self.exact(
            self.current_task_id, role=role, expected_generation=expected_generation,
            expected_evidence_head=expected_evidence_head,
        )

    def dependencies(self, todo_id: int, *, role: str, declared: Iterable[int],
                     expected_generation: str = "", expected_evidence_head: str = "") -> dict[str, Any]:
        declared_ids = list(declared)
        if len(declared_ids) != len(set(declared_ids)) or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in declared_ids
        ):
            raise BoundedContextError("declared dependencies must be unique positive Todo IDs")
        root = self.exact(todo_id, role=role, expected_generation=expected_generation,
                          expected_evidence_head=expected_evidence_head)
        args = {"todo_id": todo_id, "role": role, "declared": declared_ids,
                "expected_generation": expected_generation, "expected_evidence_head": expected_evidence_head}
        if root["outcome"] != "CURRENT":
            result = {"outcome": root["outcome"], "todo": root.get("todo"), "dependencies": [],
                      "truncation": root["truncation"]}
            return self._finish("todo-dependencies", {"kind": "direct-dependency-closure", "todo_id": todo_id}, args, result)
        actual = root["todo"].get("dependencies", [])
        if not isinstance(actual, list):
            actual = []
        admitted = [value for value in declared_ids if value in actual]
        records = {(_todo_id(item)): dict(item) for item in self.todos}
        missing = [value for value in admitted if value not in records]
        outcome = "ABSENT" if missing else "CURRENT"
        result = {"outcome": outcome, "todo": root["todo"],
                  "dependencies": [records[value] for value in admitted if value in records],
                  "undeclared_dependencies_omitted": len([value for value in actual if value not in declared_ids]),
                  "declared_non_dependencies_omitted": len([value for value in declared_ids if value not in actual]),
                  "missing_declared_dependencies": missing,
                  "truncation": {"truncated": False, "omitted": 0}}
        return self._finish("todo-dependencies", {"kind": "direct-dependency-closure", "todo_id": todo_id}, args, result)

    def inventory(self, *, purpose: str, limit: int, cursor: str = "", include_bodies: bool = False) -> dict[str, Any]:
        if purpose not in INVENTORY_PURPOSES:
            raise BoundedContextError("full inventory requires an explicit administrative or planning purpose")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise BoundedContextError("inventory limit must be between 1 and 100")
        offset = _decode_cursor(cursor, self.source_generation, purpose)
        ordered = sorted((dict(item) for item in self.todos), key=lambda item: _todo_id(item) or 0)
        page = ordered[offset:offset + limit]
        summaries = [{"todo_id": _todo_id(item), "status": item.get("status"), "title": item.get("title"),
                      "record_hash": _hash(item)} for item in page]
        end = offset + len(page)
        omitted = max(0, len(ordered) - end)
        truncated = omitted > 0
        result = {"outcome": "TRUNCATED" if truncated else "CURRENT", "purpose": purpose,
                  "summaries": summaries, "bodies": page if include_bodies else [],
                  "next_cursor": _cursor(end, self.source_generation, purpose) if truncated else None,
                  "truncation": {"truncated": truncated, "returned": len(page), "omitted": omitted,
                                 "bodies_omitted": 0 if include_bodies else len(page)}}
        args = {"purpose": purpose, "limit": limit, "cursor": cursor, "include_bodies": include_bodies}
        return self._finish("todo-inventory", {"kind": "explicit-inventory", "purpose": purpose}, args, result)


def validate_review_context(value: Mapping[str, Any]) -> None:
    """Reject backlog bodies and any resource outside the candidate-bound card."""
    allowed = {"candidate_card", "plan_citations", "codegraph", "environment", "acceptance",
               "lease", "receipt_sink", "relevant_receipts"}
    if set(value) != allowed:
        raise BoundedContextError("review context must contain only candidate-bound resources")
    if any(key in value for key in ("todos", "backlog", "inventory")):
        raise BoundedContextError("review context cannot contain backlog bodies")
    for name, resource in value.items():
        if name in {"acceptance", "relevant_receipts"}:
            continue
        if not isinstance(resource, Mapping) or set(resource) != {"ref", "hash"}:
            raise BoundedContextError(f"review resource {name} must be a reference and hash")
