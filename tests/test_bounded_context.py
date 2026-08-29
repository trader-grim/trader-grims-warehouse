from __future__ import annotations

import pytest

from tgw.bounded_context import (
    BoundedContextError,
    BoundedContextService,
    validate_review_context,
)


@pytest.fixture
def service():
    events = []
    value = BoundedContextService(
        [
            {"todo_id": 1263, "title": "Historical but open", "status": "open"},
            {"todo_id": 1915, "title": "Candidate review", "status": "open", "dependencies": [1900, 1901]},
            {"todo_id": 1900, "title": "Declared dependency", "status": "done"},
            {"todo_id": 1901, "title": "Undeclared dependency", "status": "open"},
        ],
        "todo-generation-7", "evidence-head-9", current_task_id=1915,
        evidence_sink=events.append,
    )
    return value, events


def test_exact_1915_excludes_unrelated_open_1263_and_emits_evidence(service):
    service, events = service
    result = service.exact(1915, role="review")
    assert result["outcome"] == "CURRENT"
    assert result["todo"]["todo_id"] == 1915
    assert service.current(role="review")["todo"]["todo_id"] == 1915
    assert "1263" not in str(result)
    assert events[-1]["scope"]["todo_id"] == 1915
    assert events[-1]["result_hash"].startswith("sha256:")


def test_dependencies_are_included_only_when_declared(service):
    service, _ = service
    result = service.dependencies(1915, role="review", declared=[1900, 1263])
    assert [item["todo_id"] for item in result["dependencies"]] == [1900]
    assert result["undeclared_dependencies_omitted"] == 1
    assert result["declared_non_dependencies_omitted"] == 1


def test_stale_task_and_evidence_mismatch_are_distinct(service):
    service, _ = service
    stale = service.exact(1915, role="implementation", expected_generation="old")
    mismatch = service.exact(1915, role="implementation", expected_evidence_head="other")
    irrelevant = service.exact(1263, role="review")
    assert stale["outcome"] == "STALE"
    assert mismatch["outcome"] == "MISMATCHED"
    assert irrelevant["outcome"] == "OPEN_BUT_IRRELEVANT"


def test_exact_failure_never_broadens_scope(service):
    service, _ = service
    result = service.exact(9999, role="doctor")
    assert result["outcome"] == "ABSENT"
    assert result["todo"] is None
    assert "1263" not in str(result)


def test_inventory_requires_purpose_and_is_metadata_first_paginated(service):
    service, _ = service
    with pytest.raises(BoundedContextError, match="explicit"):
        service.inventory(purpose="orientation", limit=2)
    first = service.inventory(purpose="planning-inventory", limit=2)
    assert first["outcome"] == "TRUNCATED"
    assert first["bodies"] == []
    assert first["truncation"]["omitted"] == 2
    second = service.inventory(purpose="planning-inventory", limit=2, cursor=first["next_cursor"])
    assert second["outcome"] == "CURRENT"
    assert {item["todo_id"] for item in first["summaries"] + second["summaries"]} == {1263, 1900, 1901, 1915}


def test_review_context_is_candidate_bound_references_not_backlog_bodies():
    ref = {"ref": "context://resource", "hash": "sha256:" + "a" * 64}
    context = {
        "candidate_card": ref, "plan_citations": ref, "codegraph": ref,
        "environment": ref, "acceptance": ["fixture passes"], "lease": ref,
        "receipt_sink": ref, "relevant_receipts": [],
    }
    validate_review_context(context)
    context["backlog"] = [{"todo_id": 1263}]
    with pytest.raises(BoundedContextError, match="candidate-bound"):
        validate_review_context(context)
