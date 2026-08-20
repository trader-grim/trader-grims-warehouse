import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tgw.recovery_console import RecoveryConsoleMount, create_recovery_console_router

TOKEN = "recovery-secret"
TOKEN_HASH = "sha256:" + hashlib.sha256(TOKEN.encode()).hexdigest()
SOLUTION = "sha256:" + "a" * 64
CARD = "sha256:" + "b" * 64
AUTHORITY = "sha256:" + "c" * 64
RENDERER = "sha256:" + "d" * 64


def _card():
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    return {
        "request": {
            "schema": "tgw-w17-recovery-request/v1", "recovery_id": "repair-one",
            "operator": "dave", "plan": {"commit": "f" * 40, "solution_hash": SOLUTION},
            "expiry": expiry, "effects": ["diagnose-platform"],
            "receipt_sink": "sha256:" + "e" * 64, "candidate_commit": "1" * 40,
        },
        "proposal": {
            "schema": "tgw-dynamic-surface-proposal/v1", "surface_id": "repair-one",
            "request_id": "repair-one", "plan_commit": "f" * 40, "solution_hash": SOLUTION,
            "card_hash": CARD, "authority_hash": AUTHORITY, "expiry": expiry,
            "audience": "operator", "title": "Diagnose execution platform", "state": "LIVE",
            "components": [
                {"type": "heading", "id": "summary", "text": "Bound diagnosis only"},
                {"type": "input", "id": "reason", "label": "Reason", "input": {"kind": "string", "required": True}},
            ],
            "actions": [{"id": "diagnose", "label": "Run diagnosis", "decision": "diagnose-platform", "handler_id": "platform-recovery", "field_ids": ["reason"]}],
        },
        "card_hash": CARD, "authority_hash": AUTHORITY,
    }


def _client(renderer=lambda: RENDERER):
    receipts, refusals, effects = [], [], []
    card = _card()
    mount = RecoveryConsoleMount(
        token_sha256=TOKEN_HASH, receipt_sink_hash="sha256:" + "e" * 64,
        load_card=lambda _: card, renderer_version=renderer,
        handler_contracts={"platform-recovery": {"decisions": ["diagnose-platform"]}},
        handlers={"platform-recovery": lambda invocation: effects.append(dict(invocation)) or {"status": "DIAGNOSED"}},
        persist_receipt=lambda receipt: receipts.append(dict(receipt)) or {"receipt": receipt["receipt_hash"]},
        persist_refusal=lambda refusal: refusals.append(dict(refusal)),
        claim_submission=lambda invocation: {"status": "CLAIMED", "claim_hash": "sha256:" + "f" * 64},
    )
    app = FastAPI()
    app.include_router(create_recovery_console_router(mount))
    return TestClient(app), receipts, refusals, effects


def test_recovery_entry_is_separately_authenticated_and_data_only():
    client, _, refusals, _ = _client()
    assert client.get("/api/platform-recovery/repair-one").status_code == 401
    response = client.get("/api/platform-recovery/repair-one", headers={"X-TGW-Recovery-Token": TOKEN})
    assert response.status_code == 200
    assert response.json()["surface"]["renderer"]["mode"] == "data-only-local"
    assert refusals[0]["reason"] == "separate-authentication-failed"


def test_recovery_decision_cannot_gain_unrelated_effect_or_cross_operator():
    client, receipts, refusals, effects = _client()
    surface = client.get("/api/platform-recovery/repair-one", headers={"X-TGW-Recovery-Token": TOKEN}).json()["surface"]
    body = {
        "schema": "tgw-dynamic-surface-submission/v1", "surface_hash": surface["surface_hash"],
        "action_id": "diagnose", "values": {"reason": "console unavailable"},
        "operator": "dave", "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    response = client.post("/api/platform-recovery/repair-one/decisions", json=body, headers={"X-TGW-Recovery-Token": TOKEN})
    assert response.status_code == 200
    assert receipts and effects[0]["decision"] == "diagnose-platform"
    body["operator"] = "mallory"
    assert client.post("/api/platform-recovery/repair-one/decisions", json=body, headers={"X-TGW-Recovery-Token": TOKEN}).status_code == 409
    assert "operator identity mismatch" in refusals[-1]["reason"]


def test_missing_renderer_holds_instead_of_falling_back_to_generic_form():
    client, _, refusals, _ = _client(renderer=lambda: "missing")
    response = client.get("/api/platform-recovery/repair-one", headers={"X-TGW-Recovery-Token": TOKEN})
    assert response.status_code == 409
    assert "renderer version" in refusals[-1]["reason"]
