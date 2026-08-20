from datetime import datetime, timedelta, timezone

import pytest

from tgw.dynamic_surface import DynamicSurfaceError, compile_dynamic_surface, submit_dynamic_surface

HASH = "sha256:" + "a" * 64
CARD = "sha256:" + "b" * 64
AUTHORITY = "sha256:" + "c" * 64
RENDERER = "sha256:" + "d" * 64


def _proposal(**updates):
    now = datetime.now(timezone.utc)
    value = {
        "schema": "tgw-dynamic-surface-proposal/v1", "surface_id": "repair-one",
        "request_id": "request-one", "plan_commit": "f" * 40,
        "solution_hash": HASH, "card_hash": CARD, "authority_hash": AUTHORITY,
        "expiry": (now + timedelta(minutes=5)).isoformat(), "audience": "operator",
        "title": "Review exact repair", "state": "LIVE",
        "components": [
            {"type": "heading", "id": "summary", "text": "Bound repair"},
            {"type": "evidence", "id": "evidence", "items": [HASH]},
            {"type": "input", "id": "reason", "label": "Reason", "input": {"kind": "string", "required": True}},
            {"type": "input", "id": "mode", "label": "Mode", "input": {"kind": "choice", "required": True, "choices": ["rollback", "diagnose"]}},
        ],
        "actions": [{"id": "approve-repair", "label": "Approve repair", "decision": "approve", "handler_id": "recovery-decision", "field_ids": ["reason", "mode"]}],
    }
    value.update(updates)
    return value, now


def _compile(**updates):
    proposal, now = _proposal(**updates)
    return compile_dynamic_surface(
        proposal=proposal, handler_registry={"recovery-decision": {"decisions": ["approve", "hold"]}},
        renderer_version=RENDERER, observed_at=now.isoformat(),
    )


def test_compiles_closed_data_only_surface_with_reconstruction_hashes():
    surface = _compile()
    assert surface["status"] == "LIVE"
    assert surface["renderer"]["mode"] == "data-only-local"
    assert surface["surface_hash"].startswith("sha256:")
    assert surface["presentation_hash"] != surface["render_hash"]


@pytest.mark.parametrize("mutation, message", [
    ({"components": [{"type": "html", "id": "x", "text": "<b>x</b>"}]}, "not allowlisted"),
    ({"title": "Fetch https://example.invalid"}, "remote resource"),
    ({"actions": [{"id": "run", "label": "Run", "decision": "approve", "handler_id": "shell", "field_ids": []}]}, "not registered"),
])
def test_rejects_html_remote_resources_and_unregistered_effects(mutation, message):
    with pytest.raises(DynamicSurfaceError, match=message):
        _compile(**mutation)


def test_submission_binds_surface_card_authority_handler_and_immutable_sink():
    surface = _compile()
    seen, persisted = {}, []
    receipt = submit_dynamic_surface(
        surface=surface,
        submission={
            "schema": "tgw-dynamic-surface-submission/v1", "surface_hash": surface["surface_hash"],
            "action_id": "approve-repair", "values": {"reason": "verified", "mode": "rollback"},
            "operator": "dave", "submitted_at": datetime.now(timezone.utc).isoformat(),
        },
        current_card_hash=CARD, current_authority_hash=AUTHORITY,
        handlers={"recovery-decision": lambda invocation: seen.setdefault("invocation", dict(invocation)) or {"status": "ACCEPTED"}},
        persist_receipt=lambda value: persisted.append(dict(value)) or {"sink_hash": value["receipt_hash"]},
        claim_submission=lambda invocation: {"status": "CLAIMED", "claim_hash": HASH},
    )
    assert receipt["decision"] == "approve"
    assert receipt["sink"]["sink_hash"] == receipt["receipt_hash"]
    assert seen["invocation"]["values"]["mode"] == "rollback"
    assert [item["status"] for item in persisted] == ["PENDING", "FINALIZED"]
    assert persisted[0]["receipt_hash"] == persisted[1]["pending_receipt_hash"]


def test_expired_replayed_or_cross_card_submission_is_inert():
    surface = _compile()
    submission = {
        "schema": "tgw-dynamic-surface-submission/v1", "surface_hash": surface["surface_hash"],
        "action_id": "approve-repair", "values": {"reason": "verified", "mode": "rollback"},
        "operator": "dave", "submitted_at": surface["source"]["expiry"],
    }
    with pytest.raises(DynamicSurfaceError, match="expired"):
        submit_dynamic_surface(surface=surface, submission=submission, current_card_hash=CARD,
                               current_authority_hash=AUTHORITY, handlers={}, persist_receipt=lambda _: {},
                               claim_submission=lambda _: {"status": "CLAIMED", "claim_hash": HASH})
    submission["submitted_at"] = datetime.now(timezone.utc).isoformat()
    with pytest.raises(DynamicSurfaceError, match="stale or superseded"):
        submit_dynamic_surface(surface=surface, submission=submission, current_card_hash="sha256:" + "e" * 64,
                               current_authority_hash=AUTHORITY, handlers={}, persist_receipt=lambda _: {},
                               claim_submission=lambda _: {"status": "CLAIMED", "claim_hash": HASH})


def test_submission_requires_claim_and_persists_pending_before_effect():
    surface = _compile()
    submission = {
        "schema": "tgw-dynamic-surface-submission/v1", "surface_hash": surface["surface_hash"],
        "action_id": "approve-repair", "values": {"reason": "verified", "mode": "diagnose"},
        "operator": "dave", "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    effects, persisted = [], []
    with pytest.raises(DynamicSurfaceError, match="claim boundary"):
        submit_dynamic_surface(
            surface=surface, submission=submission, current_card_hash=CARD,
            current_authority_hash=AUTHORITY,
            handlers={"recovery-decision": lambda value: effects.append(value) or {}},
            persist_receipt=lambda value: persisted.append(value) or {"ok": True},
            claim_submission=None,
        )
    assert effects == persisted == []

    submit_dynamic_surface(
        surface=surface, submission=submission, current_card_hash=CARD,
        current_authority_hash=AUTHORITY,
        handlers={"recovery-decision": lambda value: effects.append(value) or {"ok": True}},
        persist_receipt=lambda value: persisted.append(dict(value)) or {"ok": True},
        claim_submission=lambda _: {"status": "CLAIMED", "claim_hash": HASH},
    )
    assert persisted[0]["status"] == "PENDING"
    assert len(effects) == 1
