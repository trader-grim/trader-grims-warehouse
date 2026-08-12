from pathlib import Path

from tgw.governed_coding import admission_gate, dispatch_role
from tgw.harness_registry import load_registry, observe_health

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "agent-services/catalogs/harness-providers-v1.json"


def runner(path: Path, *, fail_review=False, overclaim=False):
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json,sys\n"
        "handoff=json.load(sys.stdin)\n"
        "role=handoff['card']['role']\n"
        f"fail_review={fail_review!r}\n"
        f"overclaim={overclaim!r}\n"
        "conditions={'implementation':['implemented'],'independent-review':['reviewed'],'controller-verification':['tested','linted','controller_verified']}[role]\n"
        "if overclaim and role=='implementation': conditions=['reviewed']\n"
        "if fail_review and role=='independent-review': result={'outcome':'failed','established_conditions':[],'artifacts':[{'kind':'review','verdict':'FAIL'}]}\n"
        "else: result={'outcome':'satisfied','established_conditions':conditions,'artifacts':[{'kind':'runner','role':role}]}\n"
        "print(json.dumps(result))\n"
    )
    path.chmod(0o755)
    return str(path)


def adapters():
    return {
        "tgw-plan": ROOT / "agent-services/skills/tgw-plan",
        "promptcraft": ROOT / "agent-services/providers/promptcraft",
        "promptcraft-card-handoff": ROOT / "agent-services/providers/promptcraft/bin/promptcraft-handoff",
    }


def card_template(card_id):
    return {
        "card_id": card_id,
        "solution_id": "sha256:solution",
        "plan_commit": "fb9fee3e9db756ad0f5071525e943794bf1dab9b",
        "bindings": {
            "plan_input": {"ref": "plan:p", "hash": "sha256:p"},
            "plan_graph": {"ref": "graph:g", "hash": "sha256:g"},
            "codegraph_snapshot": {"ref": "code:c", "hash": "sha256:c"},
            "source_tree": {"ref": "git:s", "hash": "sha256:s"},
            "execution_environment": {"ref": "env:e", "hash": "sha256:e"},
            "authority_conditions": {"ref": "auth:a", "hash": "sha256:a"},
        },
        "authority": ["local source and tests only"],
        "exclusions": ["no deployment"],
        "acceptance": ["role receipt passes"],
        "receipt_sink": "receipt:r",
        "lease": {"id": "lease:l", "expires_at": "2027-08-11T23:00:00Z", "stop_policy": "hold"},
    }


def setup(tmp_path, *, fail_review=False, overclaim=False):
    registry = load_registry(REGISTRY)
    coding_runner = runner(tmp_path / "coding-runner", fail_review=fail_review, overclaim=overclaim)
    config = {"commands": {"codex-implement": [coding_runner], "controller-verify": [coding_runner]}}
    bound_adapters = adapters()
    health = observe_health(registry, coding_config=config, adapters=bound_adapters)
    return registry, health, bound_adapters


def dispatch(registry, health, bound_adapters, role, identity, **kwargs):
    return dispatch_role(
        registry,
        health,
        role=role,
        adapters=bound_adapters,
        card_template=card_template("card-" + role),
        execution_identity=identity,
        required_capabilities=["source-mutation"] if role == "implementation" else ["tests"],
        **kwargs,
    )


def test_provider_selected_dispatch_returns_hash_bound_role_receipts(tmp_path):
    registry, health, bound_adapters = setup(tmp_path)
    implementation = dispatch(registry, health, bound_adapters, "implementation", "run:impl")
    review = dispatch(
        registry,
        health,
        bound_adapters,
        "independent-review",
        "run:review",
        independent_from=[implementation["selected_provider"]],
    )
    controller = dispatch(registry, health, bound_adapters, "controller-verification", "run:controller")

    assert implementation["status"] == review["status"] == controller["status"] == "PASS"
    assert implementation["selected_provider"] == "codex-local-runner"
    assert review["selected_provider"] == "controller-local-runner"
    assert implementation["promptcraft_receipt_hash"].startswith("sha256:")
    assert admission_gate([implementation, review, controller]) == {
        "schema": "tgw-coding-admission-gate/v1",
        "allowed": True,
        "reasons": [],
        "receipt_hashes": sorted([implementation["receipt_hash"], review["receipt_hash"], controller["receipt_hash"]]),
    }


def test_failed_review_receipt_blocks_admission(tmp_path):
    registry, health, bound_adapters = setup(tmp_path, fail_review=True)
    implementation = dispatch(registry, health, bound_adapters, "implementation", "run:impl")
    review = dispatch(
        registry,
        health,
        bound_adapters,
        "independent-review",
        "run:review",
        independent_from=[implementation["selected_provider"]],
    )
    controller = dispatch(registry, health, bound_adapters, "controller-verification", "run:controller")

    assert review["status"] == "FAIL"
    assert review["established_conditions"] == []
    gate = admission_gate([implementation, review, controller])
    assert gate["allowed"] is False
    assert gate["reasons"] == ["failed-role:independent-review"]


def test_unavailable_claude_is_hold_not_fabricated_fallback(tmp_path):
    registry, health, bound_adapters = setup(tmp_path)
    receipt = dispatch(
        registry,
        health,
        bound_adapters,
        "independent-review",
        "run:review",
        independent_from=["codex-local-runner", "controller-local-runner"],
    )

    assert receipt["status"] == "HOLD"
    assert receipt["selected_provider"] is None
    assert receipt["outcome"] == "unavailable"
    considered = receipt["artifacts"][0]["considered"]
    claude = next(item for item in considered if item["provider"] == "claude-local-runner")
    assert any("not present" in reason for reason in claude["reasons"])


def test_runner_cannot_establish_conditions_outside_selected_role(tmp_path):
    registry, health, bound_adapters = setup(tmp_path, overclaim=True)
    receipt = dispatch(registry, health, bound_adapters, "implementation", "run:impl")

    assert receipt["status"] == "FAIL"
    assert receipt["outcome"] == "failed"
    assert receipt["established_conditions"] == []
    assert any(item["kind"] == "contract_failure" for item in receipt["artifacts"])


def test_same_execution_context_cannot_self_review_for_admission(tmp_path):
    registry, health, bound_adapters = setup(tmp_path)
    implementation = dispatch(registry, health, bound_adapters, "implementation", "run:same")
    review = dispatch(registry, health, bound_adapters, "independent-review", "run:same")
    controller = dispatch(registry, health, bound_adapters, "controller-verification", "run:controller")

    gate = admission_gate([implementation, review, controller])
    assert gate["allowed"] is False
    assert gate["reasons"] == ["review-context-not-independent"]
