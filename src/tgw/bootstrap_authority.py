"""One-use, exact-bound bootstrap authority for the W09 platform install.

The grant is data supplied by the approved standalone Plan process.  This
module never manufactures or broadens it.  Redemption writes one immutable
receipt with O_EXCL before an effect provider can run.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from tgw.plan_authority import TypedEffect
from tgw.platform_bootstrap import validate_platform_bootstrap_effect


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(kind: str, value: Any) -> str:
    return f"{kind}:sha256:{sha256(_canonical(value)).hexdigest()}"


@dataclass(frozen=True)
class BootstrapGrant:
    grant_id: str
    plan_commit: str
    solution_hash: str
    target_host: str
    root_id: str
    candidate_commit: str
    effect: TypedEffect
    expires_at: datetime
    deployment_uses: int
    retirement_condition: str

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "BootstrapGrant":
        required = {
            "plan_commit", "solution_hash", "target_host", "root_id",
            "candidate_commit", "effect", "expires_at", "deployment_uses",
            "retirement_condition",
        }
        if set(value) != required:
            raise ValueError(f"bootstrap grant fields must be exactly {sorted(required)}")
        effect = TypedEffect.parse(value["effect"])
        expires_at = datetime.fromisoformat(str(value["expires_at"]).replace("Z", "+00:00"))
        identities = {key: value[key] for key in required - {"effect", "expires_at", "deployment_uses"}}
        if any(not isinstance(item, str) or not item for item in identities.values()):
            raise ValueError("bootstrap identities must be non-empty strings")
        if expires_at.tzinfo is None:
            raise ValueError("bootstrap expiry must be timezone-aware")
        if value["deployment_uses"] != 1:
            raise ValueError("bootstrap authority permits exactly one deployment")
        if effect.kind.value != "approval-platform-bootstrap-deployment":
            raise ValueError("bootstrap authority permits only the exact platform bootstrap deployment")
        manifest = validate_platform_bootstrap_effect(effect.parameters)
        if (
            manifest["target_host"] != value["target_host"]
            or manifest["flake_commit"] != value["candidate_commit"]
            or manifest["plan_commit"] != value["plan_commit"]
            or manifest["solution_hash"] != value["solution_hash"]
            or manifest["retirement_condition"] != value["retirement_condition"]
            or value["root_id"] != "production-releases"
        ):
            raise ValueError("bootstrap Plan, solution, target, candidate, root, or retirement binding does not match its effect")
        payload = dict(value)
        payload["effect_hash"] = effect.effect_hash
        payload.pop("effect")
        return cls(_digest("bootstrap-grant", payload), effect=effect, expires_at=expires_at, **{key: value[key] for key in identities}, deployment_uses=1)


class BootstrapSessionAuthority:
    """Redeem one immutable grant; absence/mismatch/expiry all fail closed."""

    def __init__(self, grant: BootstrapGrant, *, receipt_path: Path, current_plan_commit: str):
        self.grant = grant
        self.receipt_path = Path(receipt_path)
        if current_plan_commit != grant.plan_commit:
            raise ValueError("bootstrap grant is bound to a different Plan commit")
        if not self.receipt_path.parent.is_dir():
            raise ValueError("bootstrap receipt directory is not provisioned")

    def consume(self, request_id: str, *, effect_hash: str, generation: str, now: datetime | None = None) -> Mapping[str, Any]:
        now = now or datetime.now(timezone.utc)
        if request_id != self.grant.grant_id:
            raise ValueError("bootstrap request identity mismatch")
        if now.tzinfo is None or now >= self.grant.expires_at:
            raise ValueError("bootstrap grant is expired")
        if effect_hash != self.grant.effect.effect_hash or generation != self.grant.effect.generation:
            raise ValueError("bootstrap effect identity or generation mismatch")
        receipt = {
            "schema": "tgw-bootstrap-consumption-receipt/v1",
            "grant_id": self.grant.grant_id,
            "plan_commit": self.grant.plan_commit,
            "solution_hash": self.grant.solution_hash,
            "target_host": self.grant.target_host,
            "root_id": self.grant.root_id,
            "candidate_commit": self.grant.candidate_commit,
            "effect_hash": effect_hash,
            "generation": generation,
            "consumed_at": now.isoformat(),
            "retirement_condition": self.grant.retirement_condition,
        }
        receipt["receipt_id"] = _digest("bootstrap-consumption", receipt)
        data = _canonical(receipt) + b"\n"
        try:
            fd = os.open(self.receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
        except FileExistsError as exc:
            raise ValueError("bootstrap grant is already consumed") from exc
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        return receipt
