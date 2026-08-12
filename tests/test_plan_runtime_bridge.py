from tgw.plan_solver import solve
from tgw.workflow import EvidenceAssertion, FingerprintResult, compile_solution_runtime

COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"


def _document(required=("base@1", "app@1")):
    return {
        "schema": "tgw-plan/v2",
        "plan_commit": COMMIT,
        "capabilities": list(required),
        "providers": [
            {"id": "base", "provides": ["base@1"]},
            {"id": "app", "provides": ["app@1"], "requires": ["base@1"]},
        ],
        "observations": [],
        "target": {"id": "bridge-fixture", "profile": "implementation", "minimum_state": "admitted", "required_capabilities": list(required)},
    }


def _agreed_solution(document=None):
    document = document or _document()
    native = solve(document)
    return solve(document, conformance_result={"available": True, "closure_hash": native["closure_hash"]})


def test_compiler_derives_next_work_from_capability_conditions_not_phase_order():
    solution = _agreed_solution()

    compiled = compile_solution_runtime(solution, current_plan_commit=COMMIT)

    assert compiled.solution_hash == solution["solution_hash"]
    assert compiled.plan_commit == COMMIT
    assert [item.treatment_id for item in compiled.runtime_graph.eligible_treatments] == ["establish:base@1"]
    assert [item.treatment_id for item in compiled.runtime_graph.waiting_treatments] == ["establish:app@1"]
    app = next(item for item in compiled.treatments if item.identity == "establish:app@1")
    assert tuple(requirement.condition_id for requirement in app.requires) == ("plan.solution-dispatchable", "app@1", "base@1")


def test_new_evidence_re_evaluation_makes_dependent_work_eligible():
    solution = _agreed_solution()
    evidence = (EvidenceAssertion("base@1", FingerprintResult.TRUE, ("receipt accepted",), ()),)

    compiled = compile_solution_runtime(solution, current_plan_commit=COMMIT, capability_assertions=evidence)

    assert [item.treatment_id for item in compiled.runtime_graph.eligible_treatments] == ["establish:app@1"]


def test_unverified_conformance_blocks_every_treatment_even_with_complete_closure():
    solution = solve(_document())

    compiled = compile_solution_runtime(solution, current_plan_commit=COMMIT)

    assert solution["complete"] is True
    assert compiled.dispatchable is False
    assert compiled.holds[0].code == "BLOCKED"
    assert compiled.runtime_graph.eligible_treatments == ()
    assert all("plan.solution-dispatchable=false" in item.reasons[0] for item in compiled.runtime_graph.waiting_treatments)


def test_unknown_and_contradictory_resolution_codes_survive_compilation():
    unknown_doc = {**_document(("missing@1",)), "providers": [], "capabilities": []}
    unknown = compile_solution_runtime(solve(unknown_doc), current_plan_commit=COMMIT)
    mismatch_native = solve(_document())
    mismatch = solve(_document(), conformance_result={"available": True, "closure_hash": "sha256:different"})
    contradictory = compile_solution_runtime(mismatch, current_plan_commit=COMMIT)

    assert any(hold.code == "UNKNOWN_CAPABILITY" and hold.capability == "missing@1" for hold in unknown.holds)
    assert any(item[0] == "missing@1" and item[1] is FingerprintResult.UNKNOWN for item in unknown.runtime_graph.explicit_requirements)
    assert mismatch_native["closure_hash"] == mismatch["closure_hash"]
    assert any(hold.code == "CONTRADICTORY_RESOLUTION" for hold in contradictory.holds)
    assert any(item[1] is FingerprintResult.CONTRADICTORY for item in contradictory.runtime_graph.explicit_requirements)
