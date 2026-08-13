"""One-attempt authority dedicated to the read-only A3 observation leaf."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from tgw.a3_preintegration_observation import ObservationHold, validate_request

GRANT_SCHEMA = "tgw-read-only-observation-grant/v1"


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class ObservationAuthorityError(RuntimeError):
    pass


class ObservationAlreadyConsumed(ObservationAuthorityError):
    pass


class ObservationDispatchAmbiguous(ObservationAuthorityError):
    pass


class ObservationProvider(Protocol):
    def ready(self, request: Mapping[str, Any]) -> bool: ...
    def observe(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ReadOnlyObservationGrant:
    value: Mapping[str, Any]

    @classmethod
    def issue(
        cls,
        *,
        request: Mapping[str, Any],
        solution_sha256: str,
        closure_sha256: str,
        composition_sha256: str,
        expires_at: str,
    ) -> "ReadOnlyObservationGrant":
        request = validate_request(request)
        if solution_sha256 != request["plan"]["solution_sha256"] or closure_sha256 != request["plan"]["closure_sha256"]:
            raise ObservationAuthorityError("grant Plan solution or closure differs from request")
        payload = {
            "schema": GRANT_SCHEMA,
            "effect_kind": "tgw-prod-a3-preintegration-observation",
            "plan": {"commit": request["plan"]["commit"], "solution_sha256": solution_sha256, "closure_sha256": closure_sha256},
            "request_sha256": request["request_sha256"],
            "target": request["target"],
            "composition_sha256": composition_sha256,
            "attempts": 1,
            "expires_at": expires_at,
        }
        payload["grant_sha256"] = _hash(payload)
        return cls(cls.validate(payload))

    @staticmethod
    def validate(value: Mapping[str, Any]) -> dict[str, Any]:
        fields = {"schema", "effect_kind", "plan", "request_sha256", "target", "composition_sha256", "attempts", "expires_at", "grant_sha256"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ObservationAuthorityError("observation grant fields are not exact")
        result = dict(value)
        if result["schema"] != GRANT_SCHEMA or result["effect_kind"] != "tgw-prod-a3-preintegration-observation" or result["attempts"] != 1:
            raise ObservationAuthorityError("observation grant semantics are invalid")
        plan = result["plan"]
        if not isinstance(plan, Mapping) or set(plan) != {"commit", "solution_sha256", "closure_sha256"} or any(not isinstance(item, str) or not item for item in plan.values()):
            raise ObservationAuthorityError("observation Plan binding is invalid")
        expires = datetime.fromisoformat(str(result["expires_at"]).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            raise ObservationAuthorityError("observation expiry is not timezone aware")
        claimed = result.pop("grant_sha256")
        if claimed != _hash(result):
            raise ObservationAuthorityError("observation grant hash is invalid")
        result["grant_sha256"] = claimed
        return result


class ReadOnlyObservationController:
    """Checks readiness before atomically consuming the distinct observation token."""

    def __init__(self, *, grant: ReadOnlyObservationGrant, provider: ObservationProvider, composition_sha256: str):
        self.grant = grant
        self.provider = provider
        self.composition_sha256 = composition_sha256
        self._lock = threading.Lock()
        self._consumed = False

    @property
    def consumed(self) -> bool:
        return self._consumed

    def execute(self, request: Mapping[str, Any], *, now: datetime | None = None) -> Mapping[str, Any]:
        request = validate_request(request)
        grant = ReadOnlyObservationGrant.validate(self.grant.value)
        now = now or datetime.now(timezone.utc)
        expires = datetime.fromisoformat(str(grant["expires_at"]).replace("Z", "+00:00"))
        if now >= expires:
            raise ObservationHold("read-only observation grant expired")
        if grant["request_sha256"] != request["request_sha256"] or grant["target"] != request["target"] or grant["composition_sha256"] != self.composition_sha256:
            raise ObservationHold("read-only observation grant binding differs")
        if not self.provider.ready(request):
            raise ObservationHold("observation provider is not ready")
        with self._lock:
            if self._consumed:
                raise ObservationAlreadyConsumed("read-only observation attempt already consumed")
            self._consumed = True
        try:
            return self.provider.observe(request)
        except Exception as exc:
            raise ObservationDispatchAmbiguous("observation failed after SSH dispatch") from exc
