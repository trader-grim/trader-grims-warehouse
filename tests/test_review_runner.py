import hashlib
import json
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.execution_resources import (
    RESOURCE_SERVICE_CAPABILITIES,
    HTTPRegisteredResourceResolver,
    RegisteredResource,
    RegisteredResourceResolver,
    ResourceVerificationError,
    ed25519_public_key,
    issue_harness_retrieval_attestation,
    resource_service_catalog_hash,
)
from tgw.governed_coding import admission_gate, dispatch_role
from tgw.harness_registry import load_registry, observe_health
from tgw.review_runner import run_review, snapshot_hash

ROOT = Path(__file__).resolve().parents[1]
PROMPTCRAFT = ROOT / "agent-services/providers/promptcraft"
sys.path.insert(0, str(PROMPTCRAFT))

from promptcraft.handoff import ExecutionCard, craft_handoff  # noqa: E402

RESOURCE_SERVICE = {
    "schema": "tgw-registered-resource-service/v2",
    "id": "review-resource-service",
    "client_id": "review-test-client",
    "endpoint": "https://resources.invalid",
    "credential_env": None,
    "timeout_seconds": 5,
}
TEST_ATTESTATION_HASH = "sha256:" + "a" * 64
TEST_ATTESTATION_KEY_ID = "review-attestation-key-1"
TEST_ATTESTATION_PRIVATE_KEY = Ed25519PrivateKey.generate()


def resource_service_hash():
    return "sha256:" + hashlib.sha256(
        json.dumps(RESOURCE_SERVICE, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


RESOURCE_SERVICE_CATALOG = {
    "schema": "tgw-registered-resource-service-catalog/v3",
    "catalog_ref": "catalog:review-resource-service@1",
    "plan_commit": "fb9fee3e9db756ad0f5071525e943794bf1dab9b",
    "services": [{
        "id": RESOURCE_SERVICE["id"],
        "client_id": RESOURCE_SERVICE["client_id"],
        "descriptor_hash": resource_service_hash(),
        "capabilities": sorted(RESOURCE_SERVICE_CAPABILITIES),
        "attestation_key_id": TEST_ATTESTATION_KEY_ID,
        "attestation_public_key": ed25519_public_key(TEST_ATTESTATION_PRIVATE_KEY),
    }],
}


def snapshot(tmp_path):
    source = tmp_path / "snapshot"
    source.mkdir()
    (source / "app.py").write_text("def answer():\n    return 42\n")
    return source


def handoff(source):
    def binding(ref, content):
        return {"ref": ref, "hash": "sha256:" + hashlib.sha256(content.encode()).hexdigest()}

    plan_commit = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"
    card = ExecutionCard.create(
        {
            "card_id": "review-card",
            "solution_id": "sha256:solution",
            "role": "independent-review",
            "selected_provider": "codex-isolated-review-runner",
            "plan_commit": plan_commit,
            "resource_service": {
                "id": RESOURCE_SERVICE["id"],
                "client_id": RESOURCE_SERVICE["client_id"],
                "descriptor_hash": resource_service_hash(),
                "catalog_ref": RESOURCE_SERVICE_CATALOG["catalog_ref"],
                "catalog_hash": resource_service_catalog_hash(RESOURCE_SERVICE_CATALOG),
            },
            "bindings": {
                "plan_input": binding("plan:p", "plan input"),
                "plan_commit": binding("plan-commit:fb9", plan_commit),
                "plan_graph": binding("graph:g", "plan graph"),
                "codegraph_snapshot": binding("code:c", "code graph"),
                "source_tree": {"ref": source.resolve().as_uri(), "hash": snapshot_hash(source)},
                "execution_environment": binding("env:e", "environment"),
                "authority_conditions": binding("auth:a", "authority and conditions"),
                "candidate_evidence": binding("candidate:e", "candidate evidence"),
                "receipt_sink": binding("receipt:r", "receipt sink"),
            },
            "authority": ["read-only semantic review"],
            "exclusions": ["no source mutation", "no deployment"],
            "acceptance": ["strict report validates"],
            "receiver_profile": {"id": "codex", "version": 1},
            "lease": {"id": "lease:l", "expires_at": "2027-08-11T23:00:00Z", "stop_policy": "hold"},
        }
    )
    receipt_unsigned = {
        "schema": "tgw-execution-resource-receipt/v1",
        "card_hash": card.hash,
        "plan_commit": plan_commit,
        "resources": {name: value for name, value in sorted(card.value["bindings"].items())},
    }
    resource_receipt = {
        **receipt_unsigned,
        "receipt_hash": "sha256:" + hashlib.sha256(json.dumps(receipt_unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    return craft_handoff(
        {
            "card": card.value,
            "resource_receipt": resource_receipt,
            "resource_service": RESOURCE_SERVICE,
        },
        receiver_identity="review-context:2",
    )


def environment_preflight_receipt(bound):
    return {
        "schema": "tgw-environment-preflight-receipt/v1",
        "result": "PASS",
        "catalog_sha256": bound["card"]["bindings"]["execution_environment"]["hash"],
        "actor": "codex", "profile": "development", "attempt_id": "unit", "tools": [],
    }


def write_environment_preflight_receipt(path):
    path.write_text(json.dumps({
        "schema": "tgw-environment-preflight-receipt/v1", "result": "PASS",
        "catalog_sha256": "sha256:" + hashlib.sha256(b"environment").hexdigest(),
        "actor": "codex", "profile": "development", "attempt_id": "unit", "tools": [],
    }))


class UnitAttestedResourceResolver(HTTPRegisteredResourceResolver):
    """Unit seam for non-review role selection; live attestation uses HTTP."""

    def __init__(self, source):
        self._delegate = RegisteredResourceResolver(
            {
                "plan:p": "plan input",
                "plan-commit:fb9": "fb9fee3e9db756ad0f5071525e943794bf1dab9b",
                "graph:g": "plan graph",
                "code:c": "code graph",
                source.resolve().as_uri(): RegisteredResource(source, snapshot_hash),
                "env:e": "environment",
                "auth:a": "authority and conditions",
                "receipt:r": "receipt sink",
            }
        )

    def fetch(self, ref):
        return self._delegate.fetch(ref)

    def check_health(self, *, attestation_key_id):
        if attestation_key_id != TEST_ATTESTATION_KEY_ID:
            raise ResourceVerificationError("test resource service health identity is invalid")

    def verify_harness_retrieval_attestation(self, attestation, **kwargs):
        if attestation != {"attestation_hash": TEST_ATTESTATION_HASH}:
            raise ResourceVerificationError("test retrieval attestation is invalid")
        if (
            kwargs.get("attestation_key_id") != TEST_ATTESTATION_KEY_ID
            or kwargs.get("attestation_public_key")
            != RESOURCE_SERVICE_CATALOG["services"][0]["attestation_public_key"]
        ):
            raise ResourceVerificationError("test retrieval attestation key is invalid")
        payload = {
            "schema": "tgw-registered-resource-retrieval-attestation/v3",
            "service_id": RESOURCE_SERVICE["id"], "run_id": "unit-run",
            "client_id": RESOURCE_SERVICE["client_id"],
            "card_hash": kwargs["card_hash"], "role": kwargs["role"],
            "execution_identity": kwargs["execution_identity"], "handoff_hash": kwargs["handoff_hash"],
            "resource_receipt_hash": kwargs["resource_receipt_hash"], "resources": kwargs["resources"],
            "attestation_key_id": TEST_ATTESTATION_KEY_ID,
        }
        return issue_harness_retrieval_attestation(
            payload, signing_private_key=TEST_ATTESTATION_PRIVATE_KEY,
        )


def resource_resolver(source):
    return UnitAttestedResourceResolver(source)


def backend(path, verdict="PASS", mutate=False):
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json,pathlib,sys\n"
        "request=json.load(sys.stdin)\n"
        f"verdict={verdict!r}\n"
        f"mutate={mutate!r}\n"
        "if mutate: pathlib.Path('/workspace/app.py').write_text('mutated')\n"
        "findings=[] if verdict=='PASS' else [{'severity':'high','path':'app.py','line':2,'message':'incorrect result'}]\n"
        "print(json.dumps({'schema':'tgw-code-review/v1','verdict':verdict,'snapshot_hash':request['snapshot_hash'],'summary':'review complete','findings':findings}))\n"
    )
    path.chmod(0o755)
    return str(path)


def test_isolated_review_pass_establishes_reviewed_without_mutating_source(tmp_path):
    source = snapshot(tmp_path)
    before = snapshot_hash(source)
    bound = handoff(source)
    result = run_review(bound, [backend(tmp_path / "review-provider")], environment_preflight_receipt=environment_preflight_receipt(bound))

    assert result["outcome"] == "satisfied"
    assert result["established_conditions"] == ["reviewed"]
    assert snapshot_hash(source) == before
    assert result["artifacts"][0]["report"]["snapshot_hash"] == before


def test_snapshot_hash_framing_distinguishes_file_boundaries(tmp_path):
    one_file = tmp_path / "one"
    two_files = tmp_path / "two"
    one_file.mkdir()
    two_files.mkdir()
    (one_file / "a").write_bytes(b"\0b\0c")
    (two_files / "a").write_bytes(b"")
    (two_files / "b").write_bytes(b"c")

    assert snapshot_hash(one_file) != snapshot_hash(two_files)


def test_snapshot_hash_includes_git_named_regular_directories(tmp_path):
    source = tmp_path / "snapshot"
    source.mkdir()
    fixture_git = source / "tests" / "fixture" / ".git"
    fixture_git.mkdir(parents=True)
    payload = fixture_git / "payload"
    payload.write_bytes(b"first")
    first = snapshot_hash(source)
    payload.write_bytes(b"second")

    assert snapshot_hash(source) != first


def test_bwrap_translates_snapshot_path_and_clears_ambient_environment(tmp_path, monkeypatch):
    source = snapshot(tmp_path)
    monkeypatch.setenv("HOST_SECRET", "must-not-cross")
    provider = tmp_path / "contract-provider"
    provider.write_text(
        "#!/usr/bin/python3\n"
        "import json,os,pathlib,sys\n"
        "r=json.load(sys.stdin)\n"
        "ok=r['snapshot_root']=='/workspace' and pathlib.Path('/workspace/app.py').is_file() and 'HOST_SECRET' not in os.environ\n"
        "finding={'severity':'high','path':'app.py','line':1,'message':'sandbox contract mismatch'}\n"
        "print(json.dumps({'schema':'tgw-code-review/v1','verdict':'PASS' if ok else 'FAIL','snapshot_hash':r['snapshot_hash'],'summary':'contract checked','findings':[] if ok else [finding]}))\n"
    )
    provider.chmod(0o755)
    bound = handoff(source)
    assert run_review(bound, [str(provider)], environment_preflight_receipt=environment_preflight_receipt(bound))["outcome"] == "satisfied"


def test_candidate_cannot_supply_provider_imports_or_cwd_modules(tmp_path):
    source = snapshot(tmp_path)
    (source / "json.py").write_text("raise RuntimeError('candidate cwd import')\n")
    (source / "src").mkdir()
    (source / "src/json.py").write_text(
        "raise RuntimeError('candidate PYTHONPATH import')\n"
    )

    bound = handoff(source)
    assert run_review(
        bound, [backend(tmp_path / "trusted-review-provider")], environment_preflight_receipt=environment_preflight_receipt(bound)
    )["outcome"] == "satisfied"


def test_failed_semantic_review_never_establishes_reviewed(tmp_path):
    source = snapshot(tmp_path)
    bound = handoff(source)
    result = run_review(
        bound, [backend(tmp_path / "review-provider", verdict="FAIL")], environment_preflight_receipt=environment_preflight_receipt(bound)
    )

    assert result["outcome"] == "failed"
    assert result["established_conditions"] == []
    assert result["artifacts"][0]["report"]["findings"][0]["severity"] == "high"


def test_provider_mutation_is_confined_and_rejected(tmp_path):
    source = snapshot(tmp_path)
    before = (source / "app.py").read_text()
    try:
        bound = handoff(source)
        run_review(
            bound, [backend(tmp_path / "review-provider", mutate=True)], environment_preflight_receipt=environment_preflight_receipt(bound)
        )
    except ValueError as exc:
        assert "Read-only file system" in str(exc)
    else:
        raise AssertionError("mutating review provider was accepted")
    assert (source / "app.py").read_text() == before


def test_review_provider_has_no_network_or_host_secret_access(tmp_path):
    source = snapshot(tmp_path)
    secret = tmp_path / "host-secret"
    secret.write_text("must-not-be-visible")
    provider = tmp_path / "probe-provider"
    provider.write_text(
        "#!/usr/bin/python3\n"
        "import json,pathlib,socket,sys\n"
        "r=json.load(sys.stdin)\n"
        f"secret=pathlib.Path({str(secret)!r}).exists()\n"
        "network=True\n"
        "try: socket.socket().connect(('127.0.0.1', 9))\n"
        "except OSError: network=False\n"
        "ok=not secret and not network\n"
        "finding={'severity':'critical','path':'app.py','line':1,'message':'sandbox escaped'}\n"
        "print(json.dumps({'schema':'tgw-code-review/v1','verdict':'PASS' if ok else 'FAIL','snapshot_hash':r['snapshot_hash'],'summary':'bounded','findings':[] if ok else [finding]}))\n"
    )
    provider.chmod(0o755)
    bound = handoff(source)
    assert run_review(bound, [str(provider)], environment_preflight_receipt=environment_preflight_receipt(bound))["outcome"] == "satisfied"


def test_expired_or_incomplete_handoff_never_launches_provider(tmp_path):
    source = snapshot(tmp_path)
    value = handoff(source)
    value["receipt"]["result"] = "HOLD"
    receipt = dict(value["receipt"])
    receipt.pop("receipt_hash")
    value["receipt"]["receipt_hash"] = "sha256:" + __import__("hashlib").sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    unsigned = dict(value)
    unsigned.pop("handoff_hash")
    value["handoff_hash"] = "sha256:" + __import__("hashlib").sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(ValueError, match="not READY"):
        run_review(value, [backend(tmp_path / "review-provider")], environment_preflight_receipt=environment_preflight_receipt(value))


def test_hung_provider_is_terminated_at_bounded_timeout(tmp_path):
    source = snapshot(tmp_path)
    provider = tmp_path / "hung-provider"
    provider.write_text("#!/usr/bin/python3\nimport time\ntime.sleep(30)\n")
    provider.chmod(0o755)
    with pytest.raises(ValueError, match="bounded timeout"):
        bound = handoff(source)
        run_review(bound, [str(provider)], timeout_seconds=0.05, environment_preflight_receipt=environment_preflight_receipt(bound))


def test_attested_fake_broker_contract_binds_runtime_credential_and_audit(tmp_path):
    source = snapshot(tmp_path)
    provider_path = Path(backend(tmp_path / "review-provider"))
    credential = tmp_path / "review-auth.json"
    credential.write_text('{"mode":"fake"}')
    def digest(path):
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    policy_value = {
        "run_id": "review-run-1",
        "allowed_hosts": ["chatgpt.com"],
        "expires_unix": int(time.time()) + 300,
        "max_connections": 2,
        "max_bytes_each_direction": 1000000,
        "runtime_sha256": digest(provider_path),
        "credential_sha256": digest(credential),
    }
    from tgw.review_egress_broker import ReviewEgressPolicy
    policy = ReviewEgressPolicy.parse(policy_value)
    attestation_unsigned = {
        "schema": "tgw-review-egress-network-attestation/v1",
        "policy_hash": policy.policy_hash,
        "direct_egress_denied": True,
        "broker_bind": {"host": "192.0.2.10", "port": 8443},
    }
    attestation = {**attestation_unsigned, "attestation_hash": "sha256:" + hashlib.sha256(json.dumps(attestation_unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    receipt_unsigned = {
        "schema": "tgw-review-egress-receipt/v1",
        "run_id": policy.run_id,
        "policy_hash": policy.policy_hash,
        "sessions": [],
    }
    receipt = {**receipt_unsigned, "receipt_hash": "sha256:" + hashlib.sha256(json.dumps(receipt_unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    bound = handoff(source)
    result = run_review(
        bound,
        [str(provider_path)],
        environment_preflight_receipt=environment_preflight_receipt(bound),
        network_egress=True,
        credential_file=credential,
        tool_root=tmp_path,
        egress_policy=policy_value,
        network_attestation=attestation,
        egress_receipt=receipt,
    )
    assert result["outcome"] == "satisfied"


def test_network_review_rejects_unbound_or_denied_attestation(tmp_path):
    source = snapshot(tmp_path)
    provider_path = Path(backend(tmp_path / "review-provider"))
    credential = tmp_path / "review-auth.json"
    credential.write_text("{}")
    def digest(path):
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    policy = {
        "run_id": "r", "allowed_hosts": ["chatgpt.com"],
        "expires_unix": int(time.time()) + 60, "max_connections": 1,
        "max_bytes_each_direction": 65536, "runtime_sha256": digest(provider_path),
        "credential_sha256": digest(credential),
    }
    with pytest.raises(ValueError, match="attestation fields"):
        bound = handoff(source)
        run_review(
            bound, [str(provider_path)],
            environment_preflight_receipt=environment_preflight_receipt(bound),
            network_egress=True, credential_file=credential, tool_root=tmp_path,
            egress_policy=policy,
        )


def adapters():
    return {
        "tgw-plan": ROOT / "agent-services/skills/tgw-plan",
        "promptcraft": PROMPTCRAFT,
        "promptcraft-card-handoff": PROMPTCRAFT / "bin/promptcraft-handoff",
    }


def card_template(source, identity):
    value = handoff(source)["card"]
    value.pop("schema")
    value.pop("card_hash")
    value.pop("role")
    value.pop("selected_provider")
    value.pop("receiver_profile")
    value["card_id"] = identity
    return value


def simple_runner(path):
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json,sys\n"
        "h=json.load(sys.stdin)\n"
        "r=h['card']['role']\n"
        "c={'implementation':['implemented'],"
        "'controller-verification':['controller_verified']}[r]\n"
        "print(json.dumps({'outcome':'satisfied',"
        "'established_conditions':c,'artifacts':[],"
        "'resource_receipt_hash':h['resource_receipt']['receipt_hash'],"
        "'resource_retrieval_attestation':{'attestation_hash':'sha256:" + "a" * 64 + "'}}))\n"
    )
    path.chmod(0o755)
    return str(path)


def test_unattested_isolated_review_cannot_admit_even_with_distinct_context(tmp_path):
    source = snapshot(tmp_path)
    registry = load_registry(ROOT / "agent-services/catalogs/harness-providers-v1.json")
    provider = backend(tmp_path / "review-provider")
    preflight = tmp_path / "preflight.json"
    write_environment_preflight_receipt(preflight)
    wrapper = [
        sys.executable,
        "-m",
        "tgw.review_runner",
        "--provider-command-json",
        json.dumps([provider]),
        "--environment-preflight-receipt", str(preflight),
    ]
    local = simple_runner(tmp_path / "local-runner")
    config = {"commands": {"codex-implement": [local], "controller-verify": [local], "harness-review": wrapper}}
    bound = adapters()
    health = observe_health(registry, coding_config=config, adapters=bound)
    common = {
        "registry": registry, "health": health, "adapters": bound,
        "resource_resolver": resource_resolver(source), "resource_service": RESOURCE_SERVICE,
        "resource_service_catalog": RESOURCE_SERVICE_CATALOG,
    }
    implementation = dispatch_role(
        **common,
        role="implementation",
        card_template=card_template(source, "implementation-card"),
        execution_identity="codex-context:implementation",
        required_capabilities=["source-mutation"],
    )
    review = dispatch_role(
        **common,
        role="independent-review",
        card_template=card_template(source, "review-card"),
        execution_identity="codex-context:review",
        required_capabilities=["isolated-snapshot-review"],
    )
    controller = dispatch_role(
        **common,
        role="controller-verification",
        card_template=card_template(source, "controller-card"),
        execution_identity="controller-context:verify",
        required_capabilities=["tests"],
    )

    providers = {item["id"]: item for item in registry["providers"]}
    assert providers[implementation["selected_provider"]]["vendor_family"] == "codex"
    assert providers[review["selected_provider"]]["vendor_family"] == "codex"
    assert implementation["execution_identity"] != review["execution_identity"]
    assert review["status"] == "FAIL", json.dumps(review, indent=2)
    assert "did not return a service-issued retrieval attestation" in review["artifacts"][-1]["detail"]
    assert admission_gate([implementation, review, controller])["allowed"] is False


def test_failed_isolated_review_blocks_governed_admission(tmp_path):
    source = snapshot(tmp_path)
    registry = load_registry(ROOT / "agent-services/catalogs/harness-providers-v1.json")
    failing = backend(tmp_path / "review-provider", verdict="FAIL")
    preflight = tmp_path / "preflight.json"
    write_environment_preflight_receipt(preflight)
    wrapper = [sys.executable, "-m", "tgw.review_runner", "--provider-command-json", json.dumps([failing]), "--environment-preflight-receipt", str(preflight)]
    local = simple_runner(tmp_path / "local-runner")
    config = {"commands": {"codex-implement": [local], "controller-verify": [local], "harness-review": wrapper}}
    bound = adapters()
    health = observe_health(registry, coding_config=config, adapters=bound)
    common = {
        "registry": registry, "health": health, "adapters": bound,
        "resource_resolver": resource_resolver(source), "resource_service": RESOURCE_SERVICE,
        "resource_service_catalog": RESOURCE_SERVICE_CATALOG,
    }
    implementation = dispatch_role(**common, role="implementation", card_template=card_template(source, "i"), execution_identity="ctx:i", required_capabilities=["source-mutation"])
    review = dispatch_role(**common, role="independent-review", card_template=card_template(source, "r"), execution_identity="ctx:r", required_capabilities=["isolated-snapshot-review"])
    controller = dispatch_role(**common, role="controller-verification", card_template=card_template(source, "c"), execution_identity="ctx:c", required_capabilities=["tests"])

    assert review["status"] == "FAIL"
    gate = admission_gate([implementation, review, controller])
    assert gate["allowed"] is False
    assert gate["reasons"] == ["failed-role:independent-review"]
