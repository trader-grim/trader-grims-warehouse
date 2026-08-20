import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tgw.recovery_console_host import (
    RecoveryHostError,
    _ConfiguredRecoveryProvider,
    configured_recovery_mount,
    create_recovery_app,
)


def _hash_bytes(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _config(tmp_path):
    cards, receipts, refusals = tmp_path / "cards", tmp_path / "receipts", tmp_path / "refusals"
    for root in (cards, receipts, refusals):
        root.mkdir(parents=True)
    from tgw import dynamic_surface
    renderer = _hash_bytes(dynamic_surface.__file__ and open(dynamic_surface.__file__, "rb").read())
    sink = "sha256:" + "e" * 64
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    card = {
        "request": {
            "schema": "tgw-w17-recovery-request/v1", "recovery_id": "repair-one", "operator": "dave",
            "plan": {"commit": "f" * 40, "solution_hash": "sha256:" + "a" * 64},
            "expiry": expiry, "effects": ["diagnose-platform"], "receipt_sink": sink,
            "candidate_commit": "1" * 40,
        },
        "proposal": {
            "schema": "tgw-dynamic-surface-proposal/v1", "surface_id": "repair-one", "request_id": "repair-one",
            "plan_commit": "f" * 40, "solution_hash": "sha256:" + "a" * 64,
            "card_hash": "sha256:" + "b" * 64, "authority_hash": "sha256:" + "c" * 64,
            "expiry": expiry, "audience": "operator", "title": "Bound diagnosis", "state": "LIVE",
            "components": [{"type": "input", "id": "reason", "label": "Reason", "input": {"kind": "string", "required": True}}],
            "actions": [{"id": "diagnose", "label": "Diagnose", "decision": "diagnose-platform", "handler_id": "platform-recovery", "field_ids": ["reason"]}],
        },
        "card_hash": "sha256:" + "b" * 64, "authority_hash": "sha256:" + "c" * 64,
    }
    (cards / "repair-one.json").write_text(json.dumps(card))
    return {
        "platform_recovery": {
            "schema": "tgw-platform-recovery-host/v1", "token_sha256": _hash_bytes(b"secret"),
            "card_root": str(cards), "receipt_root": str(receipts), "refusal_root": str(refusals),
            "receipt_sink_hash": sink, "renderer_sha256": renderer,
            "provider": {
                "schema": "tgw-platform-recovery-provider-binding/v1",
                "provider_id": "tgw-platform-recovery-provider@1", "endpoint": "http://127.0.0.1:7444",
                "credential_env": "TGW_TEST_RECOVERY_TOKEN", "timeout_seconds": 3,
            },
        },
    }


def test_standalone_recovery_host_claims_before_fixed_provider(tmp_path, monkeypatch):
    config = _config(tmp_path)
    calls = []
    monkeypatch.setattr(
        _ConfiguredRecoveryProvider, "invoke",
        lambda self, invocation: calls.append(dict(invocation)) or {
            "schema": "tgw-platform-recovery-provider-response/v1", "status": "DIAGNOSED",
        },
    )
    client = TestClient(create_recovery_app(config))
    surface = client.get("/api/platform-recovery/repair-one", headers={"X-TGW-Recovery-Token": "secret"}).json()["surface"]
    body = {
        "schema": "tgw-dynamic-surface-submission/v1", "surface_hash": surface["surface_hash"],
        "action_id": "diagnose", "values": {"reason": "normal console unavailable"}, "operator": "dave",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    first = client.post("/api/platform-recovery/repair-one/decisions", json=body, headers={"X-TGW-Recovery-Token": "secret"})
    assert first.status_code == 200 and len(calls) == 1
    assert calls[0]["recovery"]["candidate_commit"] == "1" * 40
    assert calls[0]["recovery"]["plan"] == {
        "commit": "f" * 40, "solution_hash": "sha256:" + "a" * 64,
    }
    second = client.post("/api/platform-recovery/repair-one/decisions", json=body, headers={"X-TGW-Recovery-Token": "secret"})
    assert second.status_code == 409 and len(calls) == 1


def test_recovery_host_refuses_tmp_and_renderer_drift(tmp_path):
    config = _config(tmp_path)
    config["platform_recovery"]["receipt_root"] = "/tmp/recovery"
    with pytest.raises(RecoveryHostError, match="outside /tmp"):
        configured_recovery_mount(config)
    config = _config(tmp_path / "other")
    config["platform_recovery"]["renderer_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(RecoveryHostError, match="renderer"):
        configured_recovery_mount(config)
