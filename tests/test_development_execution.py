import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tgw import development_execution
from tgw.governed_coding import dispatch_role
from tgw.harness_registry import observe_health
from tgw.queue.worker_base import HardFailure


def canonical_hash(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True,
    ).stdout.strip()


def test_lifecycle_uses_distinct_role_worktrees_and_checkpoints_implementation(tmp_path, monkeypatch):
    monkeypatch.setattr(development_execution, "_WORKTREE_ROOT", type(development_execution._WORKTREE_ROOT)(tmp_path / "worktrees"))
    monkeypatch.setattr(development_execution, "_ATTEMPT_ROOT", type(development_execution._ATTEMPT_ROOT)(tmp_path / "attempts"))
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init")
    (repository / "README.md").write_text("initial\n")
    git(repository, "add", "README.md")
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial"],
        cwd=repository, check=True, capture_output=True, text=True,
    )
    source = git(repository, "rev-parse", "HEAD")
    plan_commit = "a" * 40
    registry = {
        "schema": "tgw-harness-provider-registry/v1", "id": "test-providers",
        "providers": [{
            "id": "replaceable-provider", "receiver_profile": {"id": "generic", "version": 1},
            "qualified_roles": list(development_execution._ROLE_ORDER),
            "capabilities": ["source-mutation", "tests", "isolated-snapshot-review"],
            "runner": {"kind": "configured-argv", "key": "replaceable"},
            "adapter_requirements": [], "preference": 1,
        }],
    }
    registry_path = tmp_path / "providers.json"
    registry_path.write_text(json.dumps(registry))
    adapter = tmp_path / "adapter"
    adapter.write_text("adapter")
    cards = []
    for index, role in enumerate(development_execution._ROLE_ORDER, start=1):
        key = "sha256:" + format(index, "064x")
        attempt_id = f"attempt-{index:03d}"
        cards.append({
            "idempotency_key": key, "unit": "site", "role": role,
            "root": {"kind": "Plan", "id": "PLAN-SITE"},
            "plan": {"commit": plan_commit, "solution_hash": "sha256:" + "b" * 64},
            "execution_identity": f"site:{role}:{index}",
            "allocation": {
                "attempt_id": attempt_id,
                "worktree": str(tmp_path / "worktrees" / "development-job" / attempt_id / "worktree"),
                "attempt_root": str(tmp_path / "attempts" / "development-job" / attempt_id),
            },
            "execution_card_template": {
                "card_id": key, "solution_id": "sha256:" + "b" * 64,
                "plan_commit": plan_commit, "authority": ["source only"],
                "exclusions": ["no deploy"], "acceptance": ["pass role"],
                "lease": {"id": key, "expires_at": "2027-01-01T00:00:00Z", "stop_policy": "hold"},
            },
        })
    lifecycle = {
        "request": {"request_id": "development-job", "original_request": "Build the site", "scope": "site"},
        "resolution": {"status": "RESOLVED"}, "launch_cards": cards,
    }
    lifecycle["lifecycle_hash"] = canonical_hash(lifecycle)
    document = {
        "request_id": "development-job", "lifecycle": lifecycle,
        "execution": {
            "schema": "tgw-development-execution/v1",
            "development_request_hash": lifecycle["lifecycle_hash"],
            "source_commit": source, "provider_registry_hash": canonical_hash(registry),
            "card_idempotency_keys": [card["idempotency_key"] for card in cards],
        },
    }
    config = {"coding": {
        "repository_root": str(repository),
        "commands": {"replaceable": ["/bin/true"]},
        "development": {
            "provider_registry_path": str(registry_path),
            "adapters": {"test": str(adapter)},
        },
    }}
    calls = []

    class Server:
        server_port = 12345

        def shutdown(self):
            pass

        def server_close(self):
            pass

    class Thread:
        def join(self, timeout=None):
            pass

    def fake_serve(*, card, values, root):
        assert set(values) == development_execution.CARD_RESOURCE_NAMES
        template = {
            **card["execution_card_template"],
            "resource_service": {"id": "test-service", "client_id": "test-client"},
        }
        return Server(), Thread(), template, {}, object(), {}

    def fake_dispatch(_registry, _health, *, role, execution_identity, runner_cwd, **_kwargs):
        worktree = Path(runner_cwd)
        calls.append((role, execution_identity, worktree, git(worktree, "rev-parse", "HEAD")))
        if role == "implementation":
            (worktree / "site.txt").write_text("implemented\n")
        return {"status": "PASS", "role": role, "execution_identity": execution_identity}

    monkeypatch.setattr(development_execution, "_serve_resources", fake_serve)
    monkeypatch.setattr(development_execution, "dispatch_role", fake_dispatch)
    monkeypatch.setattr(
        development_execution, "admission_gate",
        lambda receipts: {"allowed": len(receipts) == 3, "reasons": []},
    )
    result = development_execution.execute_development_lifecycle(config, document)
    assert [call[0] for call in calls] == list(development_execution._ROLE_ORDER)
    assert len({call[2] for call in calls}) == 3
    assert calls[1][3] == calls[2][3] == result["candidate"]["commit"]
    assert result["candidate"]["commit"] != source
    assert result["result_hash"] == canonical_hash({key: value for key, value in result.items() if key != "result_hash"})


@pytest.mark.parametrize("tamper", ["lifecycle", "worktree", "attempt_root"])
def test_executor_rejects_tampered_lifecycle_or_allocation_before_effects(tmp_path, monkeypatch, tamper):
    request_id = "development-job"
    attempt_id = "attempt-001"
    monkeypatch.setattr(development_execution, "_WORKTREE_ROOT", type(development_execution._WORKTREE_ROOT)(tmp_path / "worktrees"))
    monkeypatch.setattr(development_execution, "_ATTEMPT_ROOT", type(development_execution._ATTEMPT_ROOT)(tmp_path / "attempts"))
    card = {
        "idempotency_key": "sha256:" + "1" * 64, "unit": "site", "role": "implementation",
        "allocation": {
            "attempt_id": attempt_id,
            "worktree": str(tmp_path / "worktrees" / request_id / attempt_id / "worktree"),
            "attempt_root": str(tmp_path / "attempts" / request_id / attempt_id),
        },
    }
    lifecycle = {"request": {"request_id": request_id}, "launch_cards": [card], "resolution": {}}
    lifecycle["lifecycle_hash"] = canonical_hash(lifecycle)
    document = {
        "lifecycle": lifecycle,
        "execution": {
            "schema": "tgw-development-execution/v1",
            "development_request_hash": lifecycle["lifecycle_hash"],
            "card_idempotency_keys": [card["idempotency_key"]],
        },
    }
    altered = deepcopy(document)
    if tamper == "lifecycle":
        altered["lifecycle"]["resolution"] = {"status": "tampered"}
    else:
        altered["lifecycle"]["launch_cards"][0]["allocation"][tamper] = str(tmp_path / "escaped" / tamper)
        altered["lifecycle"]["lifecycle_hash"] = canonical_hash(
            {key: value for key, value in altered["lifecycle"].items() if key != "lifecycle_hash"}
        )
        altered["execution"]["development_request_hash"] = altered["lifecycle"]["lifecycle_hash"]
    monkeypatch.setattr(
        development_execution, "_load_registry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("effect reached")),
    )
    with pytest.raises(HardFailure):
        development_execution.execute_development_lifecycle({"coding": {"development": {}}}, altered)
    assert not (tmp_path / "escaped").exists()


def test_native_controller_provider_runs_from_exact_card_worktree_and_retrieves_resources(tmp_path):
    project = tmp_path / "candidate"
    project.mkdir()
    (project / "test_candidate.py").write_text("def test_candidate():\n    assert True\n")
    plan_commit = "a" * 40
    key = "sha256:" + "1" * 64
    card = {
        "idempotency_key": key, "unit": "site", "role": "controller-verification",
        "plan": {"commit": plan_commit, "solution_hash": "sha256:" + "b" * 64},
        "execution_identity": "controller:site:unique",
        "execution_card_template": {
            "card_id": key, "solution_id": "sha256:" + "b" * 64,
            "plan_commit": plan_commit, "authority": ["read and test candidate"],
            "exclusions": ["no mutation", "no deployment"],
            "acceptance": ["tests and lint pass"],
            "lease": {"id": key, "expires_at": "2027-01-01T00:00:00Z", "stop_policy": "hold"},
        },
    }
    values = {name: json.dumps({"name": name}).encode() for name in development_execution.CARD_RESOURCE_NAMES}
    values["plan_input"] = json.dumps({"request": {"original_request": "verify candidate"}}).encode()
    server, thread, template, catalog, resolver, runner_env = development_execution._serve_resources(
        card=card, values=values, root=tmp_path / "resources",
    )
    assert runner_env["TGW_ATTEMPT_ROOT"] == str(tmp_path)
    try:
        source_root = Path(__file__).resolve().parents[1]
        promptcraft = source_root / "agent-services/providers/promptcraft/bin/promptcraft-handoff"
        registry = {
            "schema": "tgw-harness-provider-registry/v1", "id": "controller-test",
            "providers": [{
                "id": "controller-local-runner", "receiver_profile": {"id": "generic", "version": 1},
                "qualified_roles": ["controller-verification"],
                "capabilities": ["tests", "lint", "receipt-verification"],
                "runner": {"kind": "configured-argv", "key": "controller-verify"},
                "adapter_requirements": ["promptcraft-card-handoff"], "preference": 1,
            }],
        }
        commands = {"commands": {"controller-verify": [
            sys.executable, "-m", "tgw.governed_role_runner", "--provider", "controller-local-runner",
        ]}}
        adapters = {"promptcraft-card-handoff": promptcraft}
        health = observe_health(registry, coding_config=commands, adapters=adapters)
        descriptor = {
            "schema": "tgw-registered-resource-service/v2",
            "id": template["resource_service"]["id"],
            "client_id": template["resource_service"]["client_id"],
            "endpoint": f"http://127.0.0.1:{server.server_port}",
            "credential_env": "TGW_DEVELOPMENT_RESOURCE_TOKEN", "timeout_seconds": 15,
        }
        receipt = dispatch_role(
            registry, health, role="controller-verification", adapters=adapters,
            card_template=template, execution_identity=card["execution_identity"],
            required_capabilities=["tests"], resource_resolver=resolver,
            resource_service=descriptor, resource_service_catalog=catalog,
            runner_environment={**runner_env, "PYTHONPATH": str(source_root / "src")},
            runner_cwd=project,
        )
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
    assert receipt["status"] == "PASS"
    assert receipt["selected_provider"] == "controller-local-runner"
    assert receipt["execution_identity"] == "controller:site:unique"
    assert receipt["harness_retrieval_attestation"]["resources"] == template["bindings"]
