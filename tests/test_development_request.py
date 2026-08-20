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


def test_each_launch_card_owns_its_allocation_snapshot():
    result = compile_request_lifecycle(request=request(), resolution=resolved(), allocation=allocation())
    first, second = result["launch_cards"][:2]
    assert first["allocation"] is not second["allocation"]
    assert first["allocation"] is not result["allocation"]
    first["allocation"]["worktree"] = "/mutated"
    assert second["allocation"]["worktree"] != "/mutated"
    assert result["allocation"]["worktree"] != "/mutated"
    assert len({card["allocation"]["worktree"] for card in result["launch_cards"]}) == len(result["launch_cards"])


def test_lifecycle_receipt_does_not_alias_caller_request_or_resolution():
    raw_request, raw_resolution = request(), resolved()
    result = compile_request_lifecycle(request=raw_request, resolution=raw_resolution, allocation=allocation())
    raw_request["constraints"].append("mutated")
    raw_resolution["alternatives"].append("mutated")
    raw_resolution["plan"]["commit"] = "c" * 40
    assert result["request"]["constraints"] == ["no live changes"]
    assert result["resolution"]["alternatives"] == ["Todo"]
    assert result["resolution"]["plan"]["commit"] == COMMIT
    assert result["lifecycle_hash"] == compile_request_lifecycle(
        request=request(), resolution=resolved(), allocation=allocation()
    )["lifecycle_hash"]


@pytest.mark.parametrize("status", ["CLARIFICATION_REQUIRED", "HELD"])
def test_ambiguous_or_held_resolution_emits_no_launch_cards(status):
    value = {"status": status, "alternatives": ["Plan", "Todo"], "confidence": 0.2, "explanation": "ambiguous", "clarification": "select a root"}
    result = compile_request_lifecycle(request=request(), resolution=value, allocation=allocation())
    assert result["launch_cards"] == []
    assert result["timeline"] == ["request-submitted", "resolution-" + status.lower().replace("_", "-")]


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


@pytest.mark.parametrize(
    ("field", "unsafe_path"),
    [
        ("worktree", "/opt/TGW/secrets/w14-request/attempt-1/worktree"),
        ("attempt_root", "/etc/w14-request/attempt-1"),
    ],
)
def test_unallowlisted_allocation_roots_are_refused(field, unsafe_path):
    unsafe = allocation()
    unsafe[field] = unsafe_path
    with pytest.raises(DevelopmentRequestError, match="isolated request-bound"):
        compile_request_lifecycle(request=request(), resolution=resolved(), allocation=unsafe)


@pytest.mark.parametrize("confidence", [True, False, float("nan"), float("inf"), -0.1, 1.1])
def test_non_numeric_or_out_of_range_confidence_is_refused(confidence):
    value = resolved()
    value["confidence"] = confidence
    with pytest.raises(DevelopmentRequestError, match="resolution summary"):
        compile_request_lifecycle(request=request(), resolution=value, allocation=allocation())


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value["plan"].update({"commit": 1}),
        lambda value: value["root"].update({"kind": []}),
        lambda value: value["closure"][1].update({"depends_on": [{}]}),
        lambda value: value["closure"][1].update({"roles": [{}]}),
    ],
)
def test_resolved_type_confusion_is_refused_with_typed_error(change):
    value = resolved()
    change(value)
    with pytest.raises(DevelopmentRequestError):
        compile_request_lifecycle(request=request(), resolution=value, allocation=allocation())


@pytest.mark.parametrize("clarification", [0, [], {}])
def test_non_string_clarification_is_refused(clarification):
    value = {"status": "HELD", "alternatives": ["Todo"], "confidence": 0.2, "explanation": "ambiguous", "clarification": clarification}
    with pytest.raises(DevelopmentRequestError, match="clarification"):
        compile_request_lifecycle(request=request(), resolution=value, allocation=allocation())


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update({"request_id": "W14"}),
        lambda value: value.update({"unexpected": "field"}),
        lambda value: value.update({"constraints": ["no live changes", "no live changes"]}),
        lambda value: value.update({"effect_limits": ["local-reversible", "local-reversible"]}),
    ],
)
def test_request_shape_and_identity_are_validated_directly(change):
    value = request()
    change(value)
    with pytest.raises(DevelopmentRequestError):
        compile_request_lifecycle(request=value, resolution=resolved(), allocation=allocation())


@pytest.mark.parametrize("alternatives", [[1], [{}], ["Todo", "Todo"]])
def test_resolution_alternatives_must_be_unique_strings(alternatives):
    value = resolved()
    value["alternatives"] = alternatives
    with pytest.raises(DevelopmentRequestError, match="resolution summary"):
        compile_request_lifecycle(request=request(), resolution=value, allocation=allocation())
