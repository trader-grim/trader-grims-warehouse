"""Separately authenticated, data-only W17 recovery entry point."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Header, HTTPException

from tgw.admission_recovery import compile_recovery_invocation
from tgw.dynamic_surface import compile_dynamic_surface, submit_dynamic_surface


@dataclass(frozen=True)
class RecoveryConsoleMount:
    token_sha256: str
    receipt_sink_hash: str
    load_card: Callable[[str], Mapping[str, Any]]
    renderer_version: Callable[[], str]
    handler_contracts: Mapping[str, Mapping[str, Any]]
    handlers: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]]
    persist_receipt: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    persist_refusal: Callable[[Mapping[str, Any]], None]
    claim_submission: Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _authenticate(mount: RecoveryConsoleMount, token: str | None, recovery_id: str) -> None:
    observed = "sha256:" + hashlib.sha256((token or "").encode()).hexdigest()
    if not hmac.compare_digest(observed, mount.token_sha256):
        mount.persist_refusal({
            "schema": "tgw-w17-recovery-refusal/v1", "recovery_id": recovery_id,
            "reason": "separate-authentication-failed", "observed_at": _now(),
        })
        raise HTTPException(401, "recovery authentication failed")


def _compiled(mount: RecoveryConsoleMount, recovery_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw = mount.load_card(recovery_id)
    if not isinstance(raw, Mapping) or set(raw) != {"request", "proposal", "card_hash", "authority_hash"}:
        raise ValueError("recovery card fields are not exact")
    request, proposal = raw["request"], raw["proposal"]
    if not isinstance(request, Mapping) or request.get("recovery_id") != recovery_id:
        raise ValueError("recovery card identity mismatch")
    invocation = compile_recovery_invocation(request=request, observed_at=_now())
    if invocation["status"] != "PREPARED":
        raise ValueError("recovery invocation is held")
    if not isinstance(proposal, Mapping) or proposal.get("card_hash") != raw["card_hash"] or proposal.get("authority_hash") != raw["authority_hash"]:
        raise ValueError("recovery surface binding mismatch")
    if proposal.get("plan_commit") != invocation["plan"]["commit"] or proposal.get("solution_hash") != invocation["plan"]["solution_hash"]:
        raise ValueError("recovery surface Plan binding mismatch")
    if invocation["receipt_sink"] != mount.receipt_sink_hash:
        raise ValueError("recovery receipt sink binding mismatch")
    actions = proposal.get("actions")
    if (
        not isinstance(actions, list) or not actions
        or any(action.get("handler_id") != "platform-recovery" for action in actions if isinstance(action, Mapping))
        or any(action.get("decision") not in invocation["effects"] for action in actions if isinstance(action, Mapping))
    ):
        raise ValueError("recovery action exceeds the exact effect set")
    surface = compile_dynamic_surface(
        proposal=proposal, handler_registry=mount.handler_contracts,
        renderer_version=mount.renderer_version(), observed_at=_now(),
    )
    return dict(raw), invocation, surface


def create_recovery_console_router(mount: RecoveryConsoleMount) -> APIRouter:
    """Mount independently of the normal console/resolver and its renderer."""
    router = APIRouter()

    @router.get("/api/platform-recovery/{recovery_id}")
    def get_recovery(recovery_id: str, x_tgw_recovery_token: str | None = Header(default=None)):
        _authenticate(mount, x_tgw_recovery_token, recovery_id)
        try:
            _card, invocation, surface = _compiled(mount, recovery_id)
        except ValueError as exc:
            mount.persist_refusal({
                "schema": "tgw-w17-recovery-refusal/v1", "recovery_id": recovery_id,
                "reason": str(exc), "observed_at": _now(),
            })
            raise HTTPException(409, str(exc)) from exc
        return {"schema": "tgw-w17-recovery-console/v1", "invocation": invocation, "surface": surface}

    @router.post("/api/platform-recovery/{recovery_id}/decisions")
    def decide_recovery(recovery_id: str, body: dict[str, Any], x_tgw_recovery_token: str | None = Header(default=None)):
        _authenticate(mount, x_tgw_recovery_token, recovery_id)
        try:
            card, invocation, surface = _compiled(mount, recovery_id)
            if body.get("operator") != invocation["operator"]:
                raise ValueError("recovery operator identity mismatch")
            receipt = submit_dynamic_surface(
                surface=surface, submission=body,
                current_card_hash=card["card_hash"], current_authority_hash=card["authority_hash"],
                handlers=mount.handlers, persist_receipt=mount.persist_receipt,
                claim_submission=mount.claim_submission,
            )
        except ValueError as exc:
            mount.persist_refusal({
                "schema": "tgw-w17-recovery-refusal/v1", "recovery_id": recovery_id,
                "reason": str(exc), "observed_at": _now(),
            })
            raise HTTPException(409, str(exc)) from exc
        return {"schema": "tgw-w17-recovery-console/v1", "invocation": invocation, "decision": receipt}

    return router
