import hashlib
import json
from pathlib import Path

from tgw.execution_resources import (
    RESOURCE_SERVICE_CAPABILITIES,
    HTTPRegisteredResourceResolver,
    RegisteredResourceResolver,
    ResourceVerificationError,
    resource_service_catalog_hash,
)
from tgw.governed_coding import admission_gate, dispatch_role
from tgw.harness_registry import load_registry, observe_health

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "agent-services/catalogs/harness-providers-v1.json"
PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"
RESOURCE_CONTENT = {
    "plan:p": "plan input",
    "plan-commit:fb9": PLAN_COMMIT,
    "graph:g": "plan graph",
    "code:c": "code graph",
    "git:s": "source tree",
    "env:e": "execution environment",
    "auth:a": "authority and conditions",
    "receipt:r": "receipt sink",
}
RESOURCE_SERVICE = {
    "schema": "tgw-registered-resource-service/v1",
    "id": "unit-resource-service",
    "endpoint": "https://resources.invalid",
    "credential_env": None,
    "timeout_seconds": 5,
}
TEST_ATTESTATION_HASH = "sha256:" + "a" * 64


def service_hash():
    return "sha256:" + hashlib.sha256(
        json.dumps(RESOURCE_SERVICE, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


RESOURCE_SERVICE_CATALOG = {
    "schema": "tgw-registered-resource-service-catalog/v2",
    "catalog_ref": "catalog:unit-resource-service@1",
    "plan_commit": PLAN_COMMIT,
    "services": [{
        "id": RESOURCE_SERVICE["id"],
        "descriptor_hash": service_hash(),
        "capabilities": sorted(RESOURCE_SERVICE_CAPABILITIES),
    }],
}


class UnitAttestedResourceResolver(HTTPRegisteredResourceResolver):
    """Inject a narrow in-process verifier for role-selection unit tests.

    Real service attestation and its echo-only rejection are exercised against
    HTTP in ``test_governed_resource_service``.  These tests isolate provider
    selection and gate behavior while retaining the mandatory dispatcher seam.
    """

    def __init__(self) -> None:
        self._delegate = RegisteredResourceResolver(RESOURCE_CONTENT)

    def fetch(self, ref):
        return self._delegate.fetch(ref)

    def verify_harness_retrieval_attestation(self, attestation, **kwargs):
        if attestation != {"attestation_hash": TEST_ATTESTATION_HASH}:
            raise ResourceVerificationError("test retrieval attestation is invalid")
        unsigned = {
            "schema": "tgw-registered-resource-retrieval-attestation/v1",
            "service_id": RESOURCE_SERVICE["id"], "run_id": "unit-run",
            **kwargs,
        }
        return {
            **unsigned,
            "attestation_hash": "sha256:" + hashlib.sha256(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }


def runner(path: Path, *, fail_review=False, overclaim=False):
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json,sys\n"
        "handoff=json.load(sys.stdin)\n"
        "role=handoff['card']['role']\n"
        f"fail_review={fail_review!r}\n"
        f"overclaim={overclaim!r}\n"
        "conditions={'implementation':['implemented'],'independent-review':['reviewed'],'controller-verification':['tested','linted','controller_verified']}[role]\n"
        "resource_receipt_hash=handoff['resource_receipt']['receipt_hash']\n"
        "attestation={'attestation_hash':'sha256:" + "a" * 64 + "'}\n"
        "if overclaim and role=='implementation': conditions=['reviewed']\n"
        "if fail_review and role=='independent-review':\n"
        " result={'outcome':'failed','established_conditions':[],'artifacts':[{'kind':'review','verdict':'FAIL'}],\n"
        " 'resource_receipt_hash':resource_receipt_hash,'resource_retrieval_attestation':attestation}\n"
        "else:\n"
        " result={'outcome':'satisfied','established_conditions':conditions,'artifacts':[{'kind':'runner','role':role}],\n"
        " 'resource_receipt_hash':resource_receipt_hash,'resource_retrieval_attestation':attestation}\n"
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
    def binding(ref):
        return {
            "ref": ref,
            "hash": "sha256:" + hashlib.sha256(RESOURCE_CONTENT[ref].encode()).hexdigest(),
        }

    return {
        "card_id": card_id,
        "solution_id": "sha256:solution",
        "plan_commit": PLAN_COMMIT,
        "resource_service": {
            "id": RESOURCE_SERVICE["id"], "descriptor_hash": service_hash(),
            "catalog_ref": RESOURCE_SERVICE_CATALOG["catalog_ref"],
            "catalog_hash": resource_service_catalog_hash(RESOURCE_SERVICE_CATALOG),
        },
        "bindings": {
            "plan_input": binding("plan:p"),
            "plan_commit": binding("plan-commit:fb9"),
            "plan_graph": binding("graph:g"),
            "codegraph_snapshot": binding("code:c"),
            "source_tree": binding("git:s"),
            "execution_environment": binding("env:e"),
            "authority_conditions": binding("auth:a"),
            "receipt_sink": binding("receipt:r"),
        },
        "authority": ["local source and tests only"],
        "exclusions": ["no deployment"],
        "acceptance": ["role receipt passes"],
        "lease": {"id": "lease:l", "expires_at": "2027-08-11T23:00:00Z", "stop_policy": "hold"},
    }


def resource_resolver():
    return UnitAttestedResourceResolver()


def setup(tmp_path, *, fail_review=False, overclaim=False):
    registry = load_registry(REGISTRY)
    coding_runner = runner(tmp_path / "coding-runner", fail_review=fail_review, overclaim=overclaim)
    config = {"commands": {"codex-implement": [coding_runner], "controller-verify": [coding_runner]}}
    bound_adapters = adapters()
    health = observe_health(registry, coding_config=config, adapters=bound_adapters)
    return registry, health, bound_adapters


def dispatch(registry, health, bound_adapters, role, identity, **kwargs):
    template = kwargs.pop("card_template", card_template("card-" + role))
    return dispatch_role(
        registry,
        health,
        role=role,
        adapters=bound_adapters,
        card_template=template,
        execution_identity=identity,
        required_capabilities=["source-mutation"] if role == "implementation" else ["tests"],
        resource_resolver=resource_resolver(),
        resource_service=RESOURCE_SERVICE,
        resource_service_catalog=RESOURCE_SERVICE_CATALOG,
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


def test_fake_nonempty_resource_hash_holds_before_promptcraft_or_runner_launch(tmp_path):
    registry, health, bound_adapters = setup(tmp_path)
    fake = card_template("fake-resource-card")
    fake["bindings"]["source_tree"]["hash"] = "sha256:" + "0" * 64

    receipt = dispatch(
        registry,
        health,
        bound_adapters,
        "implementation",
        "run:fake-resource",
        card_template=fake,
    )

    assert receipt["status"] == "HOLD"
    assert receipt["outcome"] == "resource-verification"
    assert receipt["promptcraft_receipt_hash"] is None
    assert receipt["artifacts"] == [
        {"kind": "resource_verification", "detail": "registered resource source_tree content hash mismatch"}
    ]


def test_core_dispatch_rejects_a_runner_without_a_registered_attestation_verifier(tmp_path):
    registry, health, bound_adapters = setup(tmp_path)
    receipt = dispatch_role(
        registry,
        health,
        role="implementation",
        adapters=bound_adapters,
        card_template=card_template("unattested-core-dispatch"),
        execution_identity="run:unattested-core",
        required_capabilities=["source-mutation"],
        resource_resolver=RegisteredResourceResolver(RESOURCE_CONTENT),
        resource_service=RESOURCE_SERVICE,
        resource_service_catalog=RESOURCE_SERVICE_CATALOG,
    )

    assert receipt["status"] == "FAIL"
    assert receipt["harness_retrieval_attestation_hash"] is None
    assert receipt["artifacts"][-1] == {
        "kind": "contract_failure",
        "detail": "registered resource resolver cannot verify harness retrieval attestation",
    }


def test_same_execution_context_cannot_self_review_for_admission(tmp_path):
    registry, health, bound_adapters = setup(tmp_path)
    implementation = dispatch(registry, health, bound_adapters, "implementation", "run:same")
    review = dispatch(registry, health, bound_adapters, "independent-review", "run:same")
    controller = dispatch(registry, health, bound_adapters, "controller-verification", "run:controller")

    gate = admission_gate([implementation, review, controller])
    assert gate["allowed"] is False
    assert gate["reasons"] == ["shared-execution-context:implementation,independent-review"]


def test_controller_must_use_independently_bound_execution_context(tmp_path):
    registry, health, bound_adapters = setup(tmp_path)
    implementation = dispatch(registry, health, bound_adapters, "implementation", "run:impl")
    review = dispatch(registry, health, bound_adapters, "independent-review", "run:review")
    controller = dispatch(
        registry, health, bound_adapters,
        "controller-verification", "run:review",
    )

    gate = admission_gate([implementation, review, controller])
    assert gate["allowed"] is False
    assert gate["reasons"] == [
        "shared-execution-context:controller-verification,independent-review",
    ]
