"""One-use, exact-bound bootstrap authority for the W09 platform install.

The grant is data supplied by the approved standalone Plan process.  This
module never manufactures or broadens it.  Redemption writes one immutable
receipt with O_EXCL before an effect provider can run.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
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


class BootstrapAuthorityState(str, Enum):
    UNCONSUMED = "UNCONSUMED"
    SPENT = "SPENT"
    AMBIGUOUS = "AMBIGUOUS"


class BootstrapSessionAuthority:
    """Redeem one immutable grant; absence/mismatch/expiry all fail closed."""

    def __init__(
        self,
        grant: BootstrapGrant,
        *,
        receipt_path: Path,
        current_plan_commit: str,
        trusted_uid: int = 0,
    ):
        self.grant = grant
        self.receipt_path = Path(receipt_path)
        if current_plan_commit != grant.plan_commit:
            raise ValueError("bootstrap grant is bound to a different Plan commit")
        if not self.receipt_path.is_absolute() or self.receipt_path.name in {"", ".", ".."}:
            raise ValueError("bootstrap receipt path is invalid")
        try:
            named = self.receipt_path.parent.lstat()
        except OSError as exc:
            raise ValueError("bootstrap receipt directory is not provisioned") from exc
        if (
            not stat.S_ISDIR(named.st_mode)
            or named.st_uid != trusted_uid
            or stat.S_IMODE(named.st_mode) != 0o700
        ):
            raise ValueError("bootstrap receipt directory is not provisioned")
        try:
            self._directory_fd = os.open(
                self.receipt_path.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
        except OSError as exc:
            raise ValueError("bootstrap receipt directory is not safely openable") from exc
        opened = os.fstat(self._directory_fd)
        try:
            named_after_open = self.receipt_path.parent.lstat()
        except OSError as exc:
            os.close(self._directory_fd)
            raise ValueError("bootstrap receipt directory identity is unavailable after open") from exc
        opened_identity = (opened.st_dev, opened.st_ino, opened.st_uid, stat.S_IMODE(opened.st_mode))
        named_identity = (
            named_after_open.st_dev,
            named_after_open.st_ino,
            named_after_open.st_uid,
            stat.S_IMODE(named_after_open.st_mode),
        )
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named_after_open.st_mode)
            or opened_identity != named_identity
            or opened.st_uid != trusted_uid
            or stat.S_IMODE(opened.st_mode) != 0o700
        ):
            os.close(self._directory_fd)
            raise ValueError("bootstrap receipt directory changed while opening")
        self._root_identity = opened_identity
        self._state = BootstrapAuthorityState.UNCONSUMED
        self._spent_receipt_id: str | None = None
        self._ambiguity_evidence: tuple[str, ...] = ()
        self._ambiguity_cause: Exception | None = None
        self._consume_lock = threading.Lock()

    @property
    def state(self) -> BootstrapAuthorityState:
        return self._state

    def _revalidate_root(self) -> None:
        opened = os.fstat(self._directory_fd)
        try:
            named = self.receipt_path.parent.lstat()
        except OSError as exc:
            raise OSError("bootstrap receipt directory identity is unavailable") from exc
        opened_identity = (opened.st_dev, opened.st_ino, opened.st_uid, stat.S_IMODE(opened.st_mode))
        named_identity = (named.st_dev, named.st_ino, named.st_uid, stat.S_IMODE(named.st_mode))
        if (
            opened_identity != self._root_identity
            or named_identity != self._root_identity
            or not stat.S_ISDIR(opened.st_mode)
        ):
            raise OSError("bootstrap receipt directory identity changed")

    @staticmethod
    def _write_all(fd: int, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise OSError("bootstrap consumption receipt short write")
            offset += written

    def _mark_ambiguous(
        self, receipt: Mapping[str, Any], persistence_state: str, cause: Exception
    ) -> BootstrapConsumptionAmbiguous:
        observation = {
            "schema": "tgw-bootstrap-consumption-persistence-observation/v1",
            "grant_id": self.grant.grant_id,
            "effect_hash": receipt["effect_hash"],
            "generation": receipt["generation"],
            "intended_receipt_id": receipt["receipt_id"],
            "persistence_state": persistence_state,
        }
        evidence = (_digest("bootstrap-consumption-ambiguity", observation),)
        self._state = BootstrapAuthorityState.AMBIGUOUS
        self._ambiguity_evidence = evidence
        self._ambiguity_cause = cause
        return BootstrapConsumptionAmbiguous(
            "bootstrap grant consumption persistence is ambiguous",
            evidence=evidence,
            cause=cause,
        )

    def _check_terminal_state(self) -> None:
        if self._state is BootstrapAuthorityState.SPENT:
            raise ValueError("bootstrap grant is already consumed")
        if self._state is BootstrapAuthorityState.AMBIGUOUS:
            raise BootstrapConsumptionAmbiguous(
                "bootstrap grant consumption is terminally ambiguous",
                evidence=self._ambiguity_evidence,
                cause=self._ambiguity_cause,
            )

    def _existing_is_valid(self) -> bool:
        try:
            fd = os.open(self.receipt_path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._directory_fd)
        except FileNotFoundError:
            return False
        try:
            metadata = os.fstat(fd)
            content = bytearray()
            while block := os.read(fd, 64 * 1024):
                content.extend(block)
                if len(content) > 64 * 1024:
                    return False
        finally:
            os.close(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != self._root_identity[2]
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_size != len(content)
            or not content.endswith(b"\n")
        ):
            return False
        try:
            receipt = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        fields = {
            "schema",
            "grant_id",
            "plan_commit",
            "solution_hash",
            "target_host",
            "root_id",
            "candidate_commit",
            "effect_hash",
            "generation",
            "consumed_at",
            "retirement_condition",
            "receipt_id",
        }
        if not isinstance(receipt, Mapping) or set(receipt) != fields:
            return False
        unsigned = dict(receipt)
        receipt_id = unsigned.pop("receipt_id")
        expected = {
            "schema": "tgw-bootstrap-consumption-receipt/v1",
            "grant_id": self.grant.grant_id,
            "plan_commit": self.grant.plan_commit,
            "solution_hash": self.grant.solution_hash,
            "target_host": self.grant.target_host,
            "root_id": self.grant.root_id,
            "candidate_commit": self.grant.candidate_commit,
            "effect_hash": self.grant.effect.effect_hash,
            "generation": self.grant.effect.generation,
            "retirement_condition": self.grant.retirement_condition,
        }
        return (
            all(unsigned.get(key) == value for key, value in expected.items())
            and isinstance(unsigned.get("consumed_at"), str)
            and receipt_id == _digest("bootstrap-consumption", unsigned)
            and content == _canonical(receipt) + b"\n"
        )

    @staticmethod
    def _read_exact(fd: int, expected_size: int) -> bytes:
        os.lseek(fd, 0, os.SEEK_SET)
        content = bytearray()
        while block := os.read(fd, 64 * 1024):
            content.extend(block)
            if len(content) > expected_size:
                raise OSError("bootstrap consumption receipt exceeds its exact size")
        return bytes(content)

    def _verify_named_receipt(self, original_fd: int, created: os.stat_result, expected: bytes) -> None:
        original_metadata = os.fstat(original_fd)
        original_content = self._read_exact(original_fd, len(expected))
        named_fd = os.open(
            self.receipt_path.name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=self._directory_fd,
        )
        try:
            named_metadata = os.fstat(named_fd)
            named_content = self._read_exact(named_fd, len(expected))
        finally:
            os.close(named_fd)
        identities = {
            (created.st_dev, created.st_ino),
            (original_metadata.st_dev, original_metadata.st_ino),
            (named_metadata.st_dev, named_metadata.st_ino),
        }
        expected_hash = sha256(expected).digest()
        if (
            len(identities) != 1
            or not stat.S_ISREG(original_metadata.st_mode)
            or not stat.S_ISREG(named_metadata.st_mode)
            or original_metadata.st_uid != self._root_identity[2]
            or named_metadata.st_uid != self._root_identity[2]
            or stat.S_IMODE(original_metadata.st_mode) != 0o400
            or stat.S_IMODE(named_metadata.st_mode) != 0o400
            or original_metadata.st_size != len(expected)
            or named_metadata.st_size != len(expected)
            or original_content != expected
            or named_content != expected
            or sha256(original_content).digest() != expected_hash
            or sha256(named_content).digest() != expected_hash
        ):
            raise OSError("bootstrap consumption named receipt identity or content differs")

    def consume(self, request_id: str, *, effect_hash: str, generation: str, now: datetime | None = None) -> Mapping[str, Any]:
        with self._consume_lock:
            self._check_terminal_state()
            return self._consume_unlocked(request_id, effect_hash=effect_hash, generation=generation, now=now)

    def _consume_unlocked(
        self,
        request_id: str,
        *,
        effect_hash: str,
        generation: str,
        now: datetime | None,
    ) -> Mapping[str, Any]:
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
            self._revalidate_root()
        except Exception as exc:
            raise self._mark_ambiguous(receipt, "root-not-durably-verifiable", exc) from exc
        try:
            fd = os.open(
                self.receipt_path.name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
                dir_fd=self._directory_fd,
            )
        except FileExistsError as exc:
            try:
                existing_valid = self._existing_is_valid()
            except Exception as existing_exc:
                raise self._mark_ambiguous(receipt, "existing-unobservable", existing_exc) from existing_exc
            if existing_valid:
                self._state = BootstrapAuthorityState.SPENT
                raise ValueError("bootstrap grant is already consumed") from exc
            raise self._mark_ambiguous(receipt, "existing-invalid-or-partial", exc) from exc
        except OSError as exc:
            raise self._mark_ambiguous(receipt, "not-created-or-unobservable", exc) from exc
        persistence_error: Exception | None = None
        try:
            created = os.fstat(fd)
            self._write_all(fd, data)
            os.fsync(fd)
            self._verify_named_receipt(fd, created, data)
            os.fsync(self._directory_fd)
            self._revalidate_root()
            self._verify_named_receipt(fd, created, data)
        except Exception as exc:
            persistence_error = exc
        try:
            os.close(fd)
        except Exception as exc:
            if persistence_error is None:
                persistence_error = exc
        if persistence_error is not None:
            raise self._mark_ambiguous(
                receipt, "created-not-durably-verified", persistence_error
            ) from persistence_error
        self._spent_receipt_id = receipt["receipt_id"]
        self._state = BootstrapAuthorityState.SPENT
        return receipt


class BootstrapConsumptionAmbiguous(RuntimeError):
    """Authority may be spent, but no durable consumption receipt was proved."""

    def __init__(self, message: str, *, evidence: tuple[str, ...], cause: Exception | None):
        super().__init__(message)
        self.evidence = evidence
        self.__cause__ = cause
