from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import tgw.procedure_runner as runner_module
from tgw.procedure_registry import load_procedure_registry
from tgw.procedure_runner import (
    ProcedureRunner,
    ProcedureRunnerError,
    compile_procedure_request,
    issue_deployment_approval,
)

ROOT = Path(__file__).parents[1]
REGISTRY_PATH = ROOT / "config/environment/procedures.json"
NOW = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)
PLAN = "1" * 40
SOLUTION = "sha256:" + "2" * 64
CARD = "sha256:" + "3" * 64


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    registry = load_procedure_registry(REGISTRY_PATH)
    key = Ed25519PrivateKey.generate()
    evidence = {}
    for name in registry["procedures"]["nixos-prod-switch/v1"]["preconditions"]:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"name": name}), encoding="utf-8")
        evidence[name] = {"path": str(path), "sha256": _digest(path)}
    monkeypatch.setattr(runner_module, "_EVIDENCE_ROOTS", (tmp_path,))
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=b"switched\n", stderr=b"")

    service = ProcedureRunner(
        registry=registry, public_key=key.public_key(), signer_key_id="dave-deployment-1",
        receipt_root=tmp_path, run=run, clock=lambda: NOW,
        observe=lambda _procedure, phase, request: ({
            "flake_commit": request["bindings"]["flake_commit"],
            "flake_tree": request["bindings"]["flake_tree"],
            "worktree": "clean", "system": request["bindings"]["expected_current_system"],
        } if phase == "before" else {"system": request["bindings"]["target_system"]}),
    )
    return registry, key, evidence, calls, service


def _request(registry, key, evidence, *, request_id="deploy-001", expires_at="2026-08-21T18:10:00Z", parameters=None):
    values = dict(
        request_id=request_id, procedure_id="nixos-prod-switch/v1",
        registry_revision=registry["revision"], plan_commit=PLAN,
        solution_hash=SOLUTION, card_hash=CARD, parameters=parameters or {},
        precondition_evidence=evidence,
        bindings={
            "flake_commit": "4" * 40, "flake_tree": "5" * 40,
            "expected_current_system": "/nix/store/" + "6" * 32 + "-current",
            "target_system": "/nix/store/" + "7" * 32 + "-target",
            "rollback_system": "/nix/store/" + "8" * 32 + "-rollback",
        },
    )
    unsigned = compile_procedure_request(**values)
    approval = issue_deployment_approval(
        request_hash=unsigned["request_hash"], procedure_id=unsigned["procedure_id"],
        plan_commit=PLAN, solution_hash=SOLUTION, card_hash=CARD,
        operator_id="dave", signer_key_id="dave-deployment-1",
        issued_at="2026-08-21T18:00:00Z", expires_at=expires_at,
        nonce=request_id + "-nonce", signing_private_key=key,
    )
    return compile_procedure_request(**values, approval=approval)


def test_runner_executes_only_exact_registered_argv_and_writes_receipt(tmp_path, monkeypatch):
    registry, key, evidence, calls, service = _fixture(tmp_path, monkeypatch)
    receipt = service.execute(_request(registry, key, evidence))
    assert receipt["state"] == "completed"
    assert calls[0][0] == ["nixos-rebuild", "dry-activate", "--flake", "path:/home/db/tgw-flake#tgw-prod"]
    assert calls[1][0] == ["nixos-rebuild", "switch", "--flake", "path:/home/db/tgw-flake#tgw-prod"]
    assert calls[1][1]["cwd"] == "/home/db/tgw-flake"
    assert json.loads((tmp_path / "deploy-001.json").read_text())["receipt_hash"] == receipt["receipt_hash"]


def test_runner_refuses_forged_approval_before_execution(tmp_path, monkeypatch):
    registry, key, evidence, calls, service = _fixture(tmp_path, monkeypatch)
    request = _request(registry, key, evidence, request_id="deploy-forged")
    request["approval"]["signature"] = "A" * 88
    with pytest.raises(ProcedureRunnerError, match="signature"):
        service.execute(request)
    assert calls == []
    assert (tmp_path / "deploy-forged.refusal.json").is_file()


def test_runner_refuses_stale_approval_and_replay(tmp_path, monkeypatch):
    registry, key, evidence, calls, service = _fixture(tmp_path, monkeypatch)
    stale = _request(registry, key, evidence, request_id="deploy-stale", expires_at="2026-08-21T18:00:00Z")
    with pytest.raises(ProcedureRunnerError, match="stale"):
        service.execute(stale)
    good = _request(registry, key, evidence, request_id="deploy-once")
    service.execute(good)
    with pytest.raises(ProcedureRunnerError, match="replay"):
        service.execute(good)
    assert len(calls) == 2


def test_runner_refuses_parameter_and_evidence_drift(tmp_path, monkeypatch):
    registry, key, evidence, calls, service = _fixture(tmp_path, monkeypatch)
    extra = _request(registry, key, evidence, request_id="deploy-extra", parameters={"shell": "sh"})
    with pytest.raises(ProcedureRunnerError, match="parameters"):
        service.execute(extra)
    evidence_path = Path(next(iter(evidence.values()))["path"])
    evidence_path.write_text("changed", encoding="utf-8")
    changed = _request(registry, key, evidence, request_id="deploy-changed")
    with pytest.raises(ProcedureRunnerError, match="hash differs"):
        service.execute(changed)
    assert calls == []


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("generation", "current"),
        ("generation", "releases"),
        ("generation", "operations"),
        ("generation", "receipts"),
        ("generation", "refusals"),
        ("generation", ".stage-candidate"),
        ("expected_current", ".current-candidate"),
    ],
)
def test_runner_refuses_reserved_generation_parameters(name, value):
    with pytest.raises(ProcedureRunnerError, match=f"unsafe: {name}"):
        runner_module._argv({"argv": [f":{name}"]}, {name: value})
