from copy import deepcopy

import pytest

from tgw.development_request import DevelopmentRequestError, compile_request_lifecycle

HASH = "sha256:" + "a" * 64
COMMIT = "b" * 40


def request():
    return {"request_id": "w14-request", "original_request": "Build the bounded W14 seam", "scope": "W14 only", "constraints": ["no live changes"], "effect_limits": ["local-reversible"]}


def allocation():
    return {"attempt_id": "attempt-1", "worktree": "/opt/TGW/w/attempts/w14-request/attempt-1/worktree", "attempt_root": "/var/cache/tgw/attempts/w14-request/attempt-1"}


def resolved():
    return {
        "status": "RESOLVED", "alternatives": ["Todo"], "confidence": 0.9,
        "explanation": "explicit selection", "plan": {"commit": COMMIT, "solution_hash": HASH},
        "root": {"kind": "Todo", "id": "W14"},
        "closure": [
            {"id": "W12", "depends_on": [], "roles": ["implementation"]},
            {"id": "W14", "depends_on": ["W12"], "roles": ["implementation", "independent-review"]},
        ],
    }


def test_resolved_lifecycle_is_deterministic_dependency_ordered_and_non_activating():
    first = compile_request_lifecycle(request=request(), resolution=resolved(), allocation=allocation())
    assert first == compile_request_lifecycle(request=request(), resolution=resolved(), allocation=allocation())
    assert [card["unit"] for card in first["launch_cards"]] == ["W12", "W14", "W14"]
    assert all(card["activation"] == "declarative-only" for card in first["launch_cards"])
    assert "operator-acceptance" in first["timeline"]


@pytest.mark.parametrize("status", ["CLARIFICATION_REQUIRED", "HELD"])
def test_ambiguous_or_held_resolution_emits_no_launch_cards(status):
    value = {"status": status, "alternatives": ["Plan", "Todo"], "confidence": 0.2, "explanation": "ambiguous", "clarification": "select a root"}
    result = compile_request_lifecycle(request=request(), resolution=value, allocation=allocation())
    assert result["launch_cards"] == []


def test_out_of_order_dependency_or_actor_home_allocation_is_refused():
    value = deepcopy(resolved())
    value["closure"][1]["depends_on"] = ["missing"]
    with pytest.raises(DevelopmentRequestError, match="dependency ordered"):
        compile_request_lifecycle(request=request(), resolution=value, allocation=allocation())
    unsafe = allocation()
    unsafe["worktree"] = "/home/codex/w14-request/attempt-1"
    with pytest.raises(DevelopmentRequestError, match="isolated request-bound"):
        compile_request_lifecycle(request=request(), resolution=resolved(), allocation=unsafe)


def test_attempt_path_component_and_duplicate_role_are_refused():
    unsafe = allocation()
    unsafe["worktree"] = "/opt/TGW/w/attempts/w14-request/attempt-1x/worktree"
    with pytest.raises(DevelopmentRequestError, match="isolated request-bound"):
        compile_request_lifecycle(request=request(), resolution=resolved(), allocation=unsafe)
    value = resolved()
    value["closure"][1]["roles"] = ["implementation", "implementation"]
    with pytest.raises(DevelopmentRequestError, match="roles contain duplicates"):
        compile_request_lifecycle(request=request(), resolution=value, allocation=allocation())


@pytest.mark.parametrize("confidence", [True, False, float("nan"), float("inf"), -0.1, 1.1])
def test_non_numeric_or_out_of_range_confidence_is_refused(confidence):
    value = resolved()
    value["confidence"] = confidence
    with pytest.raises(DevelopmentRequestError, match="resolution summary"):
        compile_request_lifecycle(request=request(), resolution=value, allocation=allocation())
