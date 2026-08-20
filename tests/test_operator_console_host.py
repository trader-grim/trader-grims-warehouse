import base64
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tgw.bootstrap_host_integration import BootstrapHostIntegrationError, configured_bootstrap_deployment_provider
from tgw.operator_console_host import (
    DEFAULT_PLAN_ROOT,
    ConfiguredAuthorityStore,
    _dynamic_surface_bindings,
    configured_authority_principal,
    configured_console_mount,
    configured_execution_controller,
    current_plan_commit,
    load_solution,
    plan_root,
)
from tgw.operator_console_plugin import mount_operator_console
from tgw.plan_authority import AuthorityPrincipal, PrincipalRole, TypedEffect


def _plan(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "plan"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True)
    (root / "README.md").write_text("plan\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "plan"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, text=True, capture_output=True,
    ).stdout.strip()
    return root, commit


def test_standalone_plan_default_and_exact_commit(tmp_path: Path):
    assert plan_root({}) == DEFAULT_PLAN_ROOT
    root, commit = _plan(tmp_path)
    with pytest.raises(RuntimeError, match="approved_plan_commit_required"):
        current_plan_commit(lambda: {"standalone_plan_root": root})
    assert current_plan_commit(lambda: {
        "standalone_plan_root": root, "plan_approved_commit": commit,
        "plan_approved_solution_hash": "sha256:" + "a" * 64,
    }) == commit

    (root / "README.md").write_text("later Plan state\n")
    subprocess.run(["git", "-C", str(root), "commit", "-qam", "later"], check=True)
    with pytest.raises(RuntimeError, match="approved_plan_mismatch"):
        current_plan_commit(lambda: {
            "standalone_plan_root": root, "plan_approved_commit": commit,
            "plan_approved_solution_hash": "sha256:" + "a" * 64,
        })


def test_current_plan_commit_uses_configured_git_executable(tmp_path: Path):
    root, commit = _plan(tmp_path)
    wrapper = tmp_path / "held-git"
    wrapper.write_text("#!/bin/sh\nexec git \"$@\"\n")
    wrapper.chmod(0o755)
    assert current_plan_commit(lambda: {
        "standalone_plan_root": root,
        "plan_approved_commit": commit,
        "plan_approved_solution_hash": "sha256:" + "a" * 64,
        "plan_git_path": wrapper,
    }) == commit


def test_solution_loader_fails_closed_and_checks_identity(tmp_path: Path):
    root, commit = _plan(tmp_path)
    identity = "sha256:" + "a" * 64
    directory = tmp_path / "approved-solutions"

    config = {
        "standalone_plan_root": root,
        "plan_approved_commit": commit,
        "plan_approved_solution_hash": identity,
        "plan_solution_root": directory,
    }

    def provider():
        return config
    with pytest.raises(ValueError, match="unavailable"):
        load_solution(provider, identity)
    with pytest.raises(ValueError, match="invalid"):
        load_solution(provider, "../escape")
    with pytest.raises(ValueError, match="not the approved"):
        load_solution(provider, "sha256:" + "b" * 64)
    directory.mkdir(parents=True)
    (directory / "governed-platform-solution.json").write_text(json.dumps({
        "solution_hash": identity, "plan_commit": commit,
    }))
    assert load_solution(provider, identity)["solution_hash"] == identity

    (directory / "duplicate.json").write_text(json.dumps({
        "solution_hash": identity, "plan_commit": commit,
    }))
    with pytest.raises(ValueError, match="ambiguous"):
        load_solution(provider, identity)


def test_solution_loader_rejects_an_exact_hash_bound_to_an_unapproved_plan_commit(tmp_path: Path):
    root, approved = _plan(tmp_path)
    identity = "sha256:" + "c" * 64
    directory = tmp_path / "approved-solutions"
    directory.mkdir(parents=True)
    (directory / "solution.json").write_text(json.dumps({
        "solution_hash": identity, "plan_commit": "d" * 40,
    }))
    with pytest.raises(ValueError, match="not bound"):
        load_solution(lambda: {
            "standalone_plan_root": root,
            "plan_approved_commit": approved,
            "plan_approved_solution_hash": identity,
            "plan_solution_root": directory,
        }, identity)


def test_configured_mount_is_late_bound_and_reuses_auth_functions():
    config = {}

    def operator():
        return AuthorityPrincipal("operator:fixture-alice", PrincipalRole.OPERATOR, "test-session")

    def executor():
        return AuthorityPrincipal("executor:fixture-runner", PrincipalRole.EXECUTOR, "test-credential")
    mount = configured_console_mount(
        lambda: config, require_operator=operator, require_executor=executor,
    )
    assert isinstance(mount.store, ConfiguredAuthorityStore)
    assert mount.require_operator is operator
    assert mount.require_executor is executor
    assert mount.execute_effect is not None
    with pytest.raises(RuntimeError, match="not configured"):
        mount.store.list()


def test_configured_dynamic_surface_records_same_plan_authority_decision(tmp_path: Path):
    import tgw.dynamic_surface as boundary

    plan, plan_commit = _plan(tmp_path)
    receipt_root = tmp_path / "surface-receipts"
    receipt_root.mkdir()
    transition_gate = tmp_path / "fleet-transition-gate.json"
    transition_gate.write_text(json.dumps({
        "schema": "tgw-w18-fleet-transition-gate/v1", "status": "ACTIVE",
        "transaction_id": "bootstrap", "predecessor_generation": "sha256:" + "0" * 64,
        "successor_generation": "sha256:" + "0" * 64,
    }))
    renderer_hash = "sha256:" + hashlib.sha256(Path(boundary.__file__).read_bytes()).hexdigest()
    config = {
        "standalone_plan_root": plan,
        "plan_approved_commit": plan_commit,
        "plan_approved_solution_hash": "sha256:" + "a" * 64,
        "dynamic_surfaces": {
            "renderer_sha256": renderer_hash,
            "receipt_root": str(receipt_root),
            "transition_gate_path": str(transition_gate),
        },
    }
    store = ConfiguredAuthorityStore(lambda: config)
    row = {
        "request_id": "request:sha256:" + "1" * 64,
        "plan_commit": plan_commit, "solution_hash": "sha256:" + "a" * 64,
        "closure_hash": "sha256:" + "b" * 64, "effect_hash": "effect:sha256:value",
        "effect_generation": "generation-one", "object_generation": "object-one",
        "effect_kind": "development-launch", "summary": "Review exact launch",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "decision_kind": None, "receipt_id": None,
    }
    recorded = []
    store.get = lambda request_id: row if request_id == row["request_id"] else None
    store.decide = lambda decision: recorded.append(decision) or {"decision_id": decision.decision_id}
    load, submit = _dynamic_surface_bindings(store, lambda: config)
    surface = load(row["request_id"])
    assert Path(surface["retention"]["path"]).name.endswith(".surface.json")
    retained = json.loads(Path(surface["retention"]["path"]).read_text())
    assert retained["surface_hash"] == surface["surface_hash"]
    receipt = submit(row["request_id"], {
        "schema": "tgw-dynamic-surface-submission/v1",
        "surface_hash": surface["surface_hash"], "action_id": "approve",
        "values": {"reason": "exact scope reviewed"},
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }, "operator:fixture")
    assert recorded[0].kind.value == "approve"
    assert receipt["outcome"]["decision_id"] == recorded[0].decision_id
    assert len(list(receipt_root.glob("*.json"))) == 3
    with pytest.raises(ValueError, match="replayed or is already in progress"):
        submit(row["request_id"], {
            "schema": "tgw-dynamic-surface-submission/v1",
            "surface_hash": surface["surface_hash"], "action_id": "approve",
            "values": {"reason": "exact scope reviewed"},
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }, "operator:fixture")
    assert len(recorded) == 1

    row["plan_commit"] = "f" * 40
    with pytest.raises(ValueError, match="stale Plan solution"):
        load(row["request_id"])

    row["plan_commit"] = plan_commit
    transition_gate.write_text(json.dumps({
        "schema": "tgw-w18-fleet-transition-gate/v1", "status": "QUIESCED",
    }))
    with pytest.raises(ValueError, match="suspended for a fleet transition"):
        load(row["request_id"])


def test_standard_http_mount_resolves_bootstrap_provider_after_config_load_and_before_execution():
    """Route mounting must not freeze the empty pre-lifespan config.

    This exercises the mounted `/consume` route rather than only controller
    injection.  An invalid provider binding is rejected after configuration is
    populated but before the store can begin an authority execution attempt.
    """
    config: dict[str, object] = {}
    effect = TypedEffect.parse({
        "kind": "approval-platform-bootstrap-deployment",
        "generation": "candidate-release",
        "parameters": {
            "bootstrap_contract_ref": "candidate:" + "a" * 40 + ":bootstrap-deployment:v2",
            "bootstrap_contract_hash": "sha256:" + "b" * 64,
        },
    })
    mount = configured_console_mount(
        lambda: config,
        require_operator=lambda: AuthorityPrincipal("operator:fixture", PrincipalRole.OPERATOR, "test"),
        require_executor=lambda: AuthorityPrincipal("executor:fixture", PrincipalRole.EXECUTOR, "test"),
        bootstrap_provider_factory=configured_bootstrap_deployment_provider,
    )
    mount.store.get = Mock(return_value={
        "effect_kind": effect.kind,
        "effect_generation": effect.generation,
        "effect_parameters": effect.parameters,
        "effect_hash": effect.effect_hash,
    })
    mount.store.begin_execution = Mock()
    app = FastAPI()
    mount_operator_console(app, mount)
    # This is the post-lifespan state.  The route was mounted while `config`
    # was empty, so success here proves execution resolves it lazily.
    config["pinned_bootstrap_host_integration"] = {"schema": "not-used-before-provider-validation"}
    config["bootstrap_provider_binding"] = {"schema": "wrong"}
    response = TestClient(app).post("/api/plan-authority/requests/request:fixture/consume")
    assert response.status_code == 409
    assert "provider cannot be mounted" in response.json()["detail"]
    mount.store.begin_execution.assert_not_called()


def test_allowlisted_bootstrap_provider_sends_only_contract_binding(monkeypatch):
    import tgw.bootstrap_host_integration as host

    captured: dict[str, object] = {}
    opener_handlers: list[object] = []
    binding = {
        "bootstrap_contract_ref": "candidate:" + "a" * 40 + ":bootstrap-deployment:v2",
        "bootstrap_contract_hash": "sha256:" + "b" * 64,
    }
    private = Ed25519PrivateKey.generate()
    signing_key = [private]
    public = base64.b64encode(private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    )).decode("ascii")

    class _Response:
        status = 200

        def read(self, _: int) -> bytes:
            signed = {
                "schema": "tgw-bootstrap-provider-response/v1",
                "provider_id": "tgw-bootstrap-deployment-provider@1",
                "provider_identity": "provider:tgw-prod-bootstrap",
                "provider_key_id": "bootstrap-provider-key-1",
                "operation": "observe",
                "binding": binding,
                "result": {"generation": "before", "closure": "/nix/store/fixture"},
            }
            response_hash = "sha256:" + hashlib.sha256(json.dumps(
                signed, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode()).hexdigest()
            signature = base64.b64encode(signing_key[0].sign(json.dumps(
                {**signed, "response_hash": response_hash}, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode())).decode("ascii")
            return json.dumps({**signed, "response_hash": response_hash, "signature": signature}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    class _Opener:
        def open(self, request, *, timeout: int):  # type: ignore[no-untyped-def]
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data)
            captured["authorization"] = request.headers["Authorization"]
            captured["timeout"] = timeout
            return _Response()

    monkeypatch.setenv("TGW_BOOTSTRAP_PROVIDER_TOKEN", "fixture-token")
    monkeypatch.setenv("http_proxy", "http://proxy.example.invalid:8080")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.invalid:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    def no_proxy_opener(*handlers: object) -> _Opener:
        opener_handlers.extend(handlers)
        return _Opener()

    monkeypatch.setattr(host, "build_opener", no_proxy_opener)
    provider = configured_bootstrap_deployment_provider({
        "bootstrap_provider_binding": {
            "schema": "tgw-bootstrap-provider-binding/v1",
            "provider_id": "tgw-bootstrap-deployment-provider@1",
            "endpoint": "https://bootstrap-provider.example.invalid",
            "credential_env": "TGW_BOOTSTRAP_PROVIDER_TOKEN",
            "timeout_seconds": 9,
            "provider_identity": "provider:tgw-prod-bootstrap",
            "provider_key_id": "bootstrap-provider-key-1",
            "provider_public_key": public,
        },
    })
    assert provider is not None
    assert provider.observe(binding) == {"generation": "before", "closure": "/nix/store/fixture"}
    assert captured == {
        "url": "https://bootstrap-provider.example.invalid/v1/bootstrap/observe",
        "body": binding,
        "authorization": "Bearer fixture-token",
        "timeout": 9,
    }
    assert any(isinstance(handler, host.ProxyHandler) and handler.proxies == {} for handler in opener_handlers)
    # An endpoint that can echo metadata but does not possess the pinned key
    # cannot make a bootstrap provider result authoritative.
    signing_key[0] = Ed25519PrivateKey.generate()
    with pytest.raises(BootstrapHostIntegrationError, match="signature"):
        provider.observe(binding)


def test_configured_host_principals_are_named_role_bound_and_fail_closed():
    operator = configured_authority_principal(
        {"plan_authority_operator_session_principal": "operator:alice"},
        field="plan_authority_operator_session_principal",
        role=PrincipalRole.OPERATOR,
        authentication_binding="web-session",
    )
    assert operator.identity == "operator:alice"
    assert operator.authentication_binding == "web-session"
    with pytest.raises(RuntimeError, match="not configured"):
        configured_authority_principal(
            {}, field="plan_authority_executor_principal",
            role=PrincipalRole.EXECUTOR, authentication_binding="credential-env:TEST",
        )


def test_bootstrap_host_is_unmounted_or_pin_mismatched_before_authority_execution():
    store = Mock()
    effect = TypedEffect.parse({
        "kind": "approval-platform-bootstrap-deployment",
        "generation": "candidate-release",
        "parameters": {
            "bootstrap_contract_ref": "candidate:" + "a" * 40 + ":bootstrap-deployment:v2",
            "bootstrap_contract_hash": "sha256:" + "b" * 64,
        },
    })
    controller = configured_execution_controller(store, lambda: {})
    with pytest.raises(ValueError, match="resolver is not mounted"):
        controller.execute(request_id="request:bootstrap", effect=effect, executor_principal="executor:fixture")
    store.begin_execution.assert_not_called()

    with pytest.raises(RuntimeError, match="cannot be mounted"):
        configured_execution_controller(
            store,
            lambda: {"pinned_bootstrap_host_integration": {"schema": "wrong"}},
            bootstrap_provider=object(),
        )
    store.begin_execution.assert_not_called()


def test_canonical_http_app_mounts_console_and_refuses_unpinned_docs():
    from tgw import http_server

    client = TestClient(http_server.app)
    assert client.get("/api/operator-console/discovery").status_code == 401
    site = client.get("/form/plan-authority", follow_redirects=False)
    assert site.status_code == 303
    assert site.headers["location"] == "/login?next=/form/plan-authority"
    with pytest.raises(Exception, match="approved_plan_commit_required"):
        http_server._vault_root()
