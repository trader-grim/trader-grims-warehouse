import json
import sys
from pathlib import Path

import pytest

from tgw.governed_coding import admission_gate, dispatch_role
from tgw.harness_registry import load_registry, observe_health
from tgw.review_runner import run_review, snapshot_hash

ROOT = Path(__file__).resolve().parents[1]
PROMPTCRAFT = ROOT / "agent-services/providers/promptcraft"
sys.path.insert(0, str(PROMPTCRAFT))

from promptcraft.handoff import ExecutionCard, craft_handoff  # noqa: E402


def snapshot(tmp_path):
    source = tmp_path / "snapshot"
    source.mkdir()
    (source / "app.py").write_text("def answer():\n    return 42\n")
    return source


def handoff(source):
    card = ExecutionCard.create(
        {
            "card_id": "review-card",
            "solution_id": "sha256:solution",
            "role": "independent-review",
            "selected_provider": "codex-isolated-review-runner",
            "plan_commit": "fb9fee3e9db756ad0f5071525e943794bf1dab9b",
            "bindings": {
                "plan_input": {"ref": "plan:p", "hash": "sha256:p"},
                "plan_graph": {"ref": "graph:g", "hash": "sha256:g"},
                "codegraph_snapshot": {"ref": "code:c", "hash": "sha256:c"},
                "source_tree": {"ref": source.resolve().as_uri(), "hash": snapshot_hash(source)},
                "execution_environment": {"ref": "env:e", "hash": "sha256:e"},
                "authority_conditions": {"ref": "auth:a", "hash": "sha256:a"},
            },
            "authority": ["read-only semantic review"],
            "exclusions": ["no source mutation", "no deployment"],
            "acceptance": ["strict report validates"],
            "receiver_profile": {"id": "codex", "version": 1},
            "receipt_sink": "receipt:r",
            "lease": {"id": "lease:l", "expires_at": "2027-08-11T23:00:00Z", "stop_policy": "hold"},
        }
    )
    return craft_handoff(card.value, receiver_identity="review-context:2")


def backend(path, verdict="PASS", mutate=False):
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json,pathlib,sys\n"
        "request=json.load(sys.stdin)\n"
        f"verdict={verdict!r}\n"
        f"mutate={mutate!r}\n"
        "if mutate: pathlib.Path('app.py').write_text('mutated')\n"
        "findings=[] if verdict=='PASS' else [{'severity':'high','path':'app.py','line':2,'message':'incorrect result'}]\n"
        "print(json.dumps({'schema':'tgw-code-review/v1','verdict':verdict,'snapshot_hash':request['snapshot_hash'],'summary':'review complete','findings':findings}))\n"
    )
    path.chmod(0o755)
    return str(path)


def test_isolated_review_pass_establishes_reviewed_without_mutating_source(tmp_path):
    source = snapshot(tmp_path)
    before = snapshot_hash(source)
    result = run_review(handoff(source), [backend(tmp_path / "review-provider")])

    assert result["outcome"] == "satisfied"
    assert result["established_conditions"] == ["reviewed"]
    assert snapshot_hash(source) == before
    assert result["artifacts"][0]["report"]["snapshot_hash"] == before


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
    assert run_review(handoff(source), [str(provider)])["outcome"] == "satisfied"


def test_failed_semantic_review_never_establishes_reviewed(tmp_path):
    source = snapshot(tmp_path)
    result = run_review(
        handoff(source), [backend(tmp_path / "review-provider", verdict="FAIL")]
    )

    assert result["outcome"] == "failed"
    assert result["established_conditions"] == []
    assert result["artifacts"][0]["report"]["findings"][0]["severity"] == "high"


def test_provider_mutation_is_confined_and_rejected(tmp_path):
    source = snapshot(tmp_path)
    before = (source / "app.py").read_text()
    try:
        run_review(
            handoff(source), [backend(tmp_path / "review-provider", mutate=True)]
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
    assert run_review(handoff(source), [str(provider)])["outcome"] == "satisfied"


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
        run_review(value, [backend(tmp_path / "review-provider")])


def test_hung_provider_is_terminated_at_bounded_timeout(tmp_path):
    source = snapshot(tmp_path)
    provider = tmp_path / "hung-provider"
    provider.write_text("#!/usr/bin/python3\nimport time\ntime.sleep(30)\n")
    provider.chmod(0o755)
    with pytest.raises(ValueError, match="bounded timeout"):
        run_review(handoff(source), [str(provider)], timeout_seconds=0.05)


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
        "'established_conditions':c,'artifacts':[]}))\n"
    )
    path.chmod(0o755)
    return str(path)


def test_same_vendor_different_isolated_context_is_admissible(tmp_path):
    source = snapshot(tmp_path)
    registry = load_registry(ROOT / "agent-services/catalogs/harness-providers-v1.json")
    provider = backend(tmp_path / "review-provider")
    wrapper = [
        sys.executable,
        "-m",
        "tgw.review_runner",
        "--provider-command-json",
        json.dumps([provider]),
    ]
    local = simple_runner(tmp_path / "local-runner")
    config = {"commands": {"codex-implement": [local], "controller-verify": [local], "harness-review": wrapper}}
    bound = adapters()
    health = observe_health(registry, coding_config=config, adapters=bound)
    common = {"registry": registry, "health": health, "adapters": bound}
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
    assert review["status"] == "PASS", json.dumps(review, indent=2)
    assert admission_gate([implementation, review, controller])["allowed"] is True


def test_failed_isolated_review_blocks_governed_admission(tmp_path):
    source = snapshot(tmp_path)
    registry = load_registry(ROOT / "agent-services/catalogs/harness-providers-v1.json")
    failing = backend(tmp_path / "review-provider", verdict="FAIL")
    wrapper = [sys.executable, "-m", "tgw.review_runner", "--provider-command-json", json.dumps([failing])]
    local = simple_runner(tmp_path / "local-runner")
    config = {"commands": {"codex-implement": [local], "controller-verify": [local], "harness-review": wrapper}}
    bound = adapters()
    health = observe_health(registry, coding_config=config, adapters=bound)
    common = {"registry": registry, "health": health, "adapters": bound}
    implementation = dispatch_role(**common, role="implementation", card_template=card_template(source, "i"), execution_identity="ctx:i", required_capabilities=["source-mutation"])
    review = dispatch_role(**common, role="independent-review", card_template=card_template(source, "r"), execution_identity="ctx:r", required_capabilities=["isolated-snapshot-review"])
    controller = dispatch_role(**common, role="controller-verification", card_template=card_template(source, "c"), execution_identity="ctx:c", required_capabilities=["tests"])

    assert review["status"] == "FAIL"
    gate = admission_gate([implementation, review, controller])
    assert gate["allowed"] is False
    assert gate["reasons"] == ["failed-role:independent-review"]
