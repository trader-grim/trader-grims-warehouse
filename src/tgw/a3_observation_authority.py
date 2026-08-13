"""One-attempt authority dedicated to the read-only A3 observation leaf."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from tgw.a3_preintegration_observation import (
    EvidencePersistenceAmbiguous,
    ImmutableEvidenceStore,
    ObservationHold,
    persist_evidence,
    terminal,
    validate_receipt,
    validate_request,
)

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
    def observe(self, request: Mapping[str, Any], *, on_dispatch: Any) -> Mapping[str, Any]: ...


class DurableObservationToken:
    def __init__(self, root: str, grant_sha256: str):
        self.root, self.grant_sha256 = root, grant_sha256

    def ready(self) -> bool:
        try:
            st = os.lstat(self.root)
            return stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o700
        except OSError:
            return False

    @property
    def identity(self) -> dict[str, Any]:
        st = os.lstat(self.root)
        return {"path": self.root, "uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode), "dev": st.st_dev, "ino": st.st_ino, "nlink": st.st_nlink}

    def consume(self) -> None:
        name = self.grant_sha256.split(":", 1)[1] + ".consumed"
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=root_fd)
            try:
                raw = self.grant_sha256.encode()
                if os.write(fd, raw) != len(raw):
                    raise ObservationAuthorityError("observation token write was incomplete")
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(root_fd)
        except FileExistsError as exc:
            raise ObservationAlreadyConsumed("read-only observation attempt already consumed") from exc
        finally:
            os.close(root_fd)


@dataclass(frozen=True)
class ReadOnlyObservationGrant:
    value: Mapping[str, Any]

    @classmethod
    def issue(
        cls,
        *,
        request: Mapping[str, Any],
        composition_sha256: str,
        token_root_identity: Mapping[str, Any],
        evidence_root_identity: Mapping[str, Any],
        host_state_dependency_sha256: str,
        expires_at: str,
        now: datetime | None = None,
    ) -> "ReadOnlyObservationGrant":
        request = validate_request(request)
        now = now or datetime.now(timezone.utc)
        payload = {
            "schema": GRANT_SCHEMA,
            "effect_kind": "tgw-prod-a3-preintegration-observation",
            "plan": dict(request["plan"]),
            "request_sha256": request["request_sha256"],
            "target": request["target"],
            "composition_sha256": composition_sha256,
            "token_root_identity": dict(token_root_identity),
            "evidence_root_identity": dict(evidence_root_identity),
            "host_state_dependency_sha256": host_state_dependency_sha256,
            "attempts": 1,
            "issued_at": now.isoformat(),
            "not_before": now.isoformat(),
            "expires_at": expires_at,
        }
        payload["grant_sha256"] = _hash(payload)
        return cls(cls.validate(payload))

    @staticmethod
    def validate(value: Mapping[str, Any]) -> dict[str, Any]:
        fields = {
            "schema",
            "effect_kind",
            "plan",
            "request_sha256",
            "target",
            "composition_sha256",
            "token_root_identity",
            "evidence_root_identity",
            "host_state_dependency_sha256",
            "attempts",
            "issued_at",
            "not_before",
            "expires_at",
            "grant_sha256",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ObservationAuthorityError("observation grant fields are not exact")
        result = dict(value)
        if result["schema"] != GRANT_SCHEMA or result["effect_kind"] != "tgw-prod-a3-preintegration-observation" or isinstance(result["attempts"], bool) or result["attempts"] != 1:
            raise ObservationAuthorityError("observation grant semantics are invalid")
        plan = result["plan"]
        if not isinstance(plan, Mapping) or set(plan) != {"commit", "solution_sha256", "closure_sha256"} or any(not isinstance(item, str) or not item for item in plan.values()):
            raise ObservationAuthorityError("observation Plan binding is invalid")
        issued = datetime.fromisoformat(str(result["issued_at"]).replace("Z", "+00:00"))
        not_before = datetime.fromisoformat(str(result["not_before"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(result["expires_at"]).replace("Z", "+00:00"))
        if any(item.tzinfo is None for item in (issued, not_before, expires)) or not issued <= not_before < expires or expires - issued > timedelta(minutes=10):
            raise ObservationAuthorityError("observation expiry is not timezone aware")
        claimed = result.pop("grant_sha256")
        if claimed != _hash(result):
            raise ObservationAuthorityError("observation grant hash is invalid")
        result["grant_sha256"] = claimed
        return result


class ReadOnlyObservationController:
    """Checks readiness before atomically consuming the distinct observation token."""

    def __init__(
        self,
        *,
        grant: ReadOnlyObservationGrant,
        provider: ObservationProvider,
        composition_sha256: str,
        evidence_store: ImmutableEvidenceStore | None = None,
        token: DurableObservationToken | None = None,
    ):
        self.grant = grant
        self.provider = provider
        self.composition_sha256 = composition_sha256
        self.evidence_store = evidence_store
        self.token = token
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
        not_before = datetime.fromisoformat(str(grant["not_before"]).replace("Z", "+00:00"))
        if now < not_before or now >= expires:
            raise ObservationHold("read-only observation grant expired")
        if grant["request_sha256"] != request["request_sha256"] or grant["target"] != request["target"] or grant["composition_sha256"] != self.composition_sha256:
            raise ObservationHold("read-only observation grant binding differs")
        if self.token is not None and grant["token_root_identity"] != self.token.identity:
            raise ObservationHold("observation token root differs from grant")
        if self.evidence_store is not None and grant["evidence_root_identity"] != self.evidence_store.identity:
            raise ObservationHold("observation evidence root differs from grant")
        if self.evidence_store is None or self.token is None or not self.token.ready() or not self.provider.ready(request):
            raise ObservationHold("observation provider is not ready")

        def consume() -> None:
            with self._lock:
                if self._consumed:
                    raise ObservationAlreadyConsumed("read-only observation attempt already consumed")
                self.token.consume()
                self._consumed = True

        try:
            result = self.provider.observe(request, on_dispatch=consume)
        except ObservationAlreadyConsumed:
            raise
        except Exception as exc:
            raise ObservationDispatchAmbiguous("observation failed after SSH dispatch") from exc
        if not isinstance(result, Mapping) or set(result) != {"receipt", "archive"}:
            raise ObservationDispatchAmbiguous("observation provider result is malformed")
        receipt = validate_receipt(result["receipt"], request)
        try:
            paths = persist_evidence(
                self.evidence_store,
                request=request,
                receipt=receipt,
                archive=result["archive"],
                observed_at=now.isoformat(),
            )
        except EvidencePersistenceAmbiguous as exc:
            raise ObservationDispatchAmbiguous("observation persistence is ambiguous") from exc
        completed = terminal(
            outcome="PASS",
            stage="complete",
            code="NONE",
            dispatched=True,
            request_sha256=request["request_sha256"],
            observed_at=now.isoformat(),
        )
        return {
            "schema": "tgw-prod-a3-preintegration-observation-result/v1",
            "terminal": completed,
            "receipt": receipt,
            "archive_sha256": receipt["repository"]["archive_sha256"],
            "evidence": ["observation:" + completed["terminal_sha256"], *["file:" + str(path) for path in paths]],
        }
