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
    SshObservationProvider,
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


class ObservationTokenPersistenceAmbiguous(ObservationAuthorityError):
    pass


class ObservationProvider(Protocol):
    def ready(self, request: Mapping[str, Any]) -> bool: ...
    def prepare_launch(self, request: Mapping[str, Any]) -> Any: ...


class DurableObservationToken:
    def __init__(self, root: str, grant_sha256: str):
        self.root, self.grant_sha256 = root, grant_sha256
        root_path = os.path.abspath(root)
        self._root_name = os.path.basename(root_path)
        self._parent_fd = os.open(os.path.dirname(root_path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._root_fd = os.open(self._root_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._parent_fd)

    def ready(self) -> bool:
        try:
            st = os.fstat(self._root_fd)
            return stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o700
        except OSError:
            return False

    @property
    def identity(self) -> dict[str, Any]:
        st = os.fstat(self._root_fd)
        return {"path": self.root, "uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode), "dev": st.st_dev, "ino": st.st_ino, "nlink": st.st_nlink}

    def consume(self) -> None:
        name = self.grant_sha256.split(":", 1)[1] + ".consumed"
        root_fd = self._root_fd
        created = False
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=root_fd)
            created = True
            try:
                raw = self.grant_sha256.encode()
                view = memoryview(raw)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise ObservationAuthorityError("observation token write was incomplete")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            check_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                check_st = os.fstat(check_fd)
                if not stat.S_ISREG(check_st.st_mode) or check_st.st_nlink != 1 or os.read(check_fd, len(raw) + 1) != raw:
                    raise ObservationAuthorityError("observation token readback is invalid")
            finally:
                os.close(check_fd)
            os.fsync(root_fd)
        except FileExistsError as exc:
            raise ObservationAlreadyConsumed("read-only observation attempt already consumed") from exc
        except Exception as exc:
            if created:
                raise ObservationTokenPersistenceAmbiguous("observation token durable state is uncertain") from exc
            raise
        finally:
            named = os.stat(self._root_name, dir_fd=self._parent_fd, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (os.fstat(self._root_fd).st_dev, os.fstat(self._root_fd).st_ino):
                raise ObservationAuthorityError("observation token root identity changed")


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
        host_state_dependency: Mapping[str, Any],
        expires_at: str,
        now: datetime | None = None,
    ) -> "ReadOnlyObservationGrant":
        request = validate_request(request)
        if dict(host_state_dependency) != request["host_state_dependency"]:
            raise ObservationAuthorityError("grant host-state dependency differs from request")
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
            "host_state_dependency": dict(host_state_dependency),
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
            "host_state_dependency",
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
        allow_test_provider: bool = False,
    ):
        self.grant = grant
        self.provider = provider
        self.composition_sha256 = composition_sha256
        self.evidence_store = evidence_store
        self.token = token
        if not allow_test_provider and type(provider) is not SshObservationProvider:
            raise ObservationAuthorityError("production controller requires the sealed SSH observation provider")
        self._lock = threading.Lock()
        self._consumed = False

    @property
    def consumed(self) -> bool:
        return self._consumed

    def execute(self, request: Mapping[str, Any], *, now: datetime | None = None) -> Mapping[str, Any]:
        if self._consumed:
            raise ObservationAlreadyConsumed("read-only observation attempt already consumed")
        now = now or datetime.now(timezone.utc)
        request = validate_request(request, now=now)
        grant = ReadOnlyObservationGrant.validate(self.grant.value)
        expires = datetime.fromisoformat(str(grant["expires_at"]).replace("Z", "+00:00"))
        not_before = datetime.fromisoformat(str(grant["not_before"]).replace("Z", "+00:00"))
        if now < not_before or now >= expires:
            raise ObservationHold("read-only observation grant expired")
        if grant["request_sha256"] != request["request_sha256"] or grant["target"] != request["target"] or grant["composition_sha256"] != self.composition_sha256:
            raise ObservationHold("read-only observation grant binding differs")
        if grant["plan"] != request["plan"] or grant["host_state_dependency"] != request["host_state_dependency"]:
            raise ObservationHold("observation Plan or host-state binding differs")
        if self.token is not None and grant["token_root_identity"] != self.token.identity:
            raise ObservationHold("observation token root differs from grant")
        if self.evidence_store is not None and grant["evidence_root_identity"] != self.evidence_store.identity:
            raise ObservationHold("observation evidence root differs from grant")
        if self.evidence_store is None or self.token is None or not self.token.ready():
            raise ObservationHold("observation provider is not ready")

        def consume() -> None:
            with self._lock:
                if self._consumed:
                    raise ObservationAlreadyConsumed("read-only observation attempt already consumed")
                self.token.consume()
                self._consumed = True

        try:
            launch = self.provider.prepare_launch(request)
            if not callable(launch):
                raise ObservationAuthorityError("provider did not prepare a sealed launch")
            try:
                consume()
            except ObservationTokenPersistenceAmbiguous:
                self._consumed = True
                close = getattr(launch, "close", None)
                if callable(close):
                    close()
                raise ObservationDispatchAmbiguous("token persistence is ambiguous before SSH dispatch")
            except Exception:
                close = getattr(launch, "close", None)
                if callable(close):
                    close()
                raise
            result = launch()
        except ObservationAlreadyConsumed:
            raise
        except ObservationHold:
            if not self._consumed:
                raise
            raise ObservationDispatchAmbiguous("observation held after SSH dispatch")
        except Exception as exc:
            if not self._consumed:
                raise ObservationHold("observation launch preparation failed before dispatch") from exc
            raise ObservationDispatchAmbiguous("observation failed after SSH dispatch") from exc
        if not isinstance(result, Mapping) or set(result) != {"receipt", "archive"}:
            raise ObservationDispatchAmbiguous("observation provider result is malformed")
        receipt = validate_receipt(result["receipt"], request)
        completed = terminal(
            outcome="PASS",
            stage="complete",
            code="NONE",
            dispatched=True,
            request_sha256=request["request_sha256"],
            observed_at=now.isoformat(),
        )
        try:
            paths = persist_evidence(
                self.evidence_store,
                request=request,
                receipt=receipt,
                archive=result["archive"],
                observed_at=now.isoformat(),
                attachments={
                    "grant.json": grant,
                    "token.json": {"grant_sha256": grant["grant_sha256"], "consumed": True, "identity": self.token.identity},
                    "host-state.json": request["host_state_dependency"],
                    "source.json": request["source"],
                    "terminal.json": completed,
                },
            )
        except EvidencePersistenceAmbiguous as exc:
            raise ObservationDispatchAmbiguous("observation persistence is ambiguous") from exc
        return {
            "schema": "tgw-prod-a3-preintegration-observation-result/v1",
            "terminal": completed,
            "receipt": receipt,
            "archive_sha256": receipt["repository"]["archive_sha256"],
            "evidence": [
                "observation:" + completed["terminal_sha256"],
                *["artifact:" + hashlib.sha256(path.read_bytes()).hexdigest() for path in paths],
            ],
        }
