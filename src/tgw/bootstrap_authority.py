"""One-use, exact-bound bootstrap authority for the W09 platform install.

The grant is data supplied by the approved standalone Plan process.  This
module never manufactures or broadens it.  Redemption writes one immutable
receipt with O_EXCL before an effect provider can run.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
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

_PRODUCTION_AUTHORITY_SEAL = object()


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
        if effect.parameters.get("schema") == "tgw-approval-application-bootstrap/v1":
            expected_ref = f"candidate:{value['candidate_commit']}:application-bootstrap:v1"
            matches = (
                set(effect.parameters) == {"schema", "application_contract_ref", "application_contract_hash"}
                and effect.parameters.get("application_contract_ref") == expected_ref
                and isinstance(effect.parameters.get("application_contract_hash"), str)
                and re.fullmatch(r"sha256:[0-9a-f]{64}", effect.parameters["application_contract_hash"]) is not None
                and value["plan_commit"] == "f0a8cf22b2c7b2f064292a048ffcb8ee98919e99"
                and value["solution_hash"] == "sha256:1c3684135769e5dcabcaf130c55df160a4cecc0d3ebcee6ccd129ab97cdd709b"
                and value["target_host"] == "tgw-prod"
                and value["retirement_condition"] == "W10:canonical-gate-operational"
            )
        else:
            manifest = validate_platform_bootstrap_effect(effect.parameters)
            matches = (
                manifest["target_host"] == value["target_host"]
                and manifest["flake_commit"] == value["candidate_commit"]
                and manifest["plan_commit"] == value["plan_commit"]
                and manifest["solution_hash"] == value["solution_hash"]
                and manifest["retirement_condition"] == value["retirement_condition"]
            )
        if not matches or value["root_id"] != "production-releases":
            raise ValueError("bootstrap Plan, solution, target, candidate, root, or retirement binding does not match its effect")
        payload = dict(value)
        payload["effect_hash"] = effect.effect_hash
        payload.pop("effect")
        return cls(_digest("bootstrap-grant", payload), effect=effect, expires_at=expires_at, **{key: value[key] for key in identities}, deployment_uses=1)


@dataclass(frozen=True)
class ApplicationBootstrapGrant(BootstrapGrant):
    """Disjoint W09 grant type; a historical Nix switch cannot satisfy it."""

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "ApplicationBootstrapGrant":
        grant = super().parse(value)
        if grant.effect.parameters.get("schema") != "tgw-approval-application-bootstrap/v1":
            raise ValueError("W09 application bootstrap requires the exact application effect schema")
        return grant


class BootstrapAuthorityState(str, Enum):
    UNCONSUMED = "UNCONSUMED"
    SPENT = "SPENT"
    AMBIGUOUS = "AMBIGUOUS"


class BootstrapSessionAuthority:
    """Redeem one immutable grant; absence/mismatch/expiry all fail closed."""

    __slots__ = (
        "__dict__",
        "grant", "receipt_path", "_lock_name", "_directory_fd", "_root_identity",
        "_state", "_spent_receipt_id", "_ambiguity_evidence", "_ambiguity_cause",
        "_consume_lock", "_production_authority", "_grant_artifact", "_bindings_frozen",
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("BootstrapSessionAuthority is sealed")

    def __getattribute__(self, name: str) -> Any:
        """Ignore instance-dictionary method shadows on production authority."""

        if name not in {"_production_authority", "_bindings_frozen", "__class__"}:
            try:
                production = object.__getattribute__(self, "_production_authority")
                frozen = object.__getattribute__(self, "_bindings_frozen")
            except AttributeError:
                production = frozen = False
            class_value = getattr(type(self), name, None)
            if production and frozen and callable(class_value):
                return class_value.__get__(self, type(self))
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        immutable = {
            "grant", "receipt_path", "_production_authority", "_grant_artifact",
            "_root_identity", "_directory_fd", "_lock_name",
        }
        if getattr(self, "_bindings_frozen", False) and name in immutable:
            raise AttributeError("bootstrap authority binding is immutable")
        if (
            getattr(self, "_bindings_frozen", False)
            and getattr(self, "_production_authority", False)
            and callable(getattr(type(self), name, None))
        ):
            raise AttributeError("production bootstrap authority methods cannot be shadowed")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        grant: BootstrapGrant,
        *,
        receipt_path: Path,
        current_plan_commit: str,
        trusted_uid: int = 0,
        _production_token: object | None = None,
        _grant_artifact: tuple[Path, int, bytes, tuple[int, int]] | None = None,
    ):
        self.grant = grant
        self.receipt_path = Path(receipt_path)
        if current_plan_commit != grant.plan_commit:
            raise ValueError("bootstrap grant is bound to a different Plan commit")
        if not self.receipt_path.is_absolute() or self.receipt_path.name in {"", ".", ".."}:
            raise ValueError("bootstrap receipt path is invalid")
        if len(self.receipt_path.name.encode()) > 200:
            raise ValueError("bootstrap receipt filename exceeds its exact bound")
        self._lock_name = f".{self.receipt_path.name}.lock"
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
        self._production_authority = _production_token is _PRODUCTION_AUTHORITY_SEAL
        self._grant_artifact = _grant_artifact
        if self._production_authority and (
            type(grant) is not ApplicationBootstrapGrant or _grant_artifact is None
        ):
            self.close()
            raise ValueError("production application bootstrap grant is not protected")
        self._bindings_frozen = True

    @property
    def production_authority(self) -> bool:
        return self._production_authority

    @classmethod
    def production_application(
        cls, grant_path: Path, *, receipt_path: Path,
        current_plan_commit: str, trusted_uid: int = 0,
    ) -> "BootstrapSessionAuthority":
        path = Path(grant_path)
        for ancestor in (path.parent, *path.parents):
            item = ancestor.lstat()
            if not stat.S_ISDIR(item.st_mode) or item.st_uid != 0 or stat.S_IMODE(item.st_mode) & 0o022:
                raise ValueError("production bootstrap grant ancestor is not protected")
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(fd)
            raw = os.pread(fd, 1024 * 1024 + 1, 0)
            named = os.stat(path, follow_symlinks=False)
            identity = (metadata.st_dev, metadata.st_ino)
            if (
                len(raw) > 1024 * 1024 or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0 or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o444
                or (named.st_dev, named.st_ino) != identity
            ):
                raise ValueError("production bootstrap grant artifact is unsafe")
            grant = ApplicationBootstrapGrant.parse(json.loads(raw))
        except Exception:
            os.close(fd)
            raise
        return cls(
            grant, receipt_path=receipt_path, current_plan_commit=current_plan_commit,
            trusted_uid=trusted_uid, _production_token=_PRODUCTION_AUTHORITY_SEAL,
            _grant_artifact=(path, fd, raw, identity),
        )

    def close(self) -> None:
        for name in ("_directory_fd",):
            fd = getattr(self, name, -1)
            if fd >= 0:
                try: os.close(fd)
                except OSError: pass
                object.__setattr__(self, name, -1)
        artifact = getattr(self, "_grant_artifact", None)
        if artifact is not None:
            try: os.close(artifact[1])
            except OSError: pass
            object.__setattr__(self, "_grant_artifact", None)

    def _revalidate_grant_artifact(self) -> None:
        if not self._production_authority:
            return
        path, fd, raw, identity = self._grant_artifact
        held = os.fstat(fd); named = os.stat(path, follow_symlinks=False)
        if (
            (held.st_dev, held.st_ino) != identity
            or (named.st_dev, named.st_ino) != identity
            or os.pread(fd, len(raw) + 1, 0) != raw
        ):
            raise ValueError("production bootstrap grant artifact changed")
        try:
            retained = ApplicationBootstrapGrant.parse(json.loads(raw))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("production bootstrap grant bytes are invalid") from exc
        if retained != self.grant:
            raise ValueError("production bootstrap grant object differs from held issuance")

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

    def _existing_receipt_status(self) -> str | bool | None:
        try:
            fd = os.open(self.receipt_path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._directory_fd)
        except FileNotFoundError:
            return None
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
        valid = (
            all(unsigned.get(key) == value for key, value in expected.items())
            and isinstance(unsigned.get("consumed_at"), str)
            and receipt_id == _digest("bootstrap-consumption", unsigned)
            and content == _canonical(receipt) + b"\n"
        )
        return str(receipt_id) if valid else False

    def _validate_lock_artifact(self, lock_fd: int) -> None:
        held = os.fstat(lock_fd)
        os.lseek(lock_fd, 0, os.SEEK_SET)
        held_content = os.read(lock_fd, 1)
        named_fd = os.open(self._lock_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._directory_fd)
        try:
            named = os.fstat(named_fd)
            named_content = os.read(named_fd, 1)
        finally:
            os.close(named_fd)
        if (
            not stat.S_ISREG(held.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or held.st_uid != self._root_identity[2]
            or named.st_uid != self._root_identity[2]
            or stat.S_IMODE(held.st_mode) != 0o400
            or stat.S_IMODE(named.st_mode) != 0o400
            or held.st_size != 0
            or named.st_size != 0
            or held.st_nlink != 1
            or named.st_nlink != 1
            or held_content != b""
            or named_content != b""
            or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise OSError("bootstrap consumption lock artifact identity differs")

    def _sync_and_validate_lock_lifecycle(self, lock_fd: int) -> None:
        os.fsync(lock_fd)
        os.fsync(self._directory_fd)
        self._revalidate_root()
        self._validate_lock_artifact(lock_fd)

    def _acquire_filesystem_lock(self, receipt: Mapping[str, Any]) -> int:
        try:
            self._revalidate_root()
            lock_fd = os.open(
                self._lock_name,
                os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW,
                0o400,
                dir_fd=self._directory_fd,
            )
        except Exception as exc:
            raise self._mark_ambiguous(receipt, "lock-open-unavailable", exc) from exc
        locked = False
        try:
            self._validate_lock_artifact(lock_fd)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            locked = True
            self._revalidate_root()
            self._validate_lock_artifact(lock_fd)
            self._sync_and_validate_lock_lifecycle(lock_fd)
        except Exception as exc:
            if locked:
                try:
                    self._sync_and_validate_lock_lifecycle(lock_fd)
                except Exception:
                    pass
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(lock_fd)
            except OSError:
                pass
            raise self._mark_ambiguous(receipt, "lock-not-trusted-or-acquired", exc) from exc
        return lock_fd

    def _release_filesystem_lock(self, lock_fd: int, receipt: Mapping[str, Any]) -> None:
        lifecycle_error: Exception | None = None
        try:
            self._sync_and_validate_lock_lifecycle(lock_fd)
        except Exception as exc:
            lifecycle_error = exc
        release_error: Exception | None = None
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except Exception as exc:
            release_error = exc
            try:
                os.close(lock_fd)
            except OSError:
                pass
        error = lifecycle_error or release_error
        if error is not None:
            raise self._mark_ambiguous(receipt, "lock-release-unverifiable", error) from error

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
            if self._production_authority and now is not None:
                raise ValueError("production bootstrap consumption clock is internal")
            self._revalidate_grant_artifact()
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
        lock_fd = self._acquire_filesystem_lock(receipt)
        try:
            return self._consume_with_filesystem_lock(receipt, data, lock_fd)
        finally:
            self._release_filesystem_lock(lock_fd, receipt)

    def _consume_with_filesystem_lock(
        self, receipt: Mapping[str, Any], data: bytes, lock_fd: int
    ) -> Mapping[str, Any]:
        try:
            existing_status = self._existing_receipt_status()
        except Exception as exc:
            raise self._mark_ambiguous(receipt, "existing-unobservable-after-lock", exc) from exc
        if isinstance(existing_status, str):
            self._spent_receipt_id = existing_status
            self._state = BootstrapAuthorityState.SPENT
            raise ValueError("bootstrap grant is already consumed")
        if existing_status is False:
            raise self._mark_ambiguous(
                receipt,
                "existing-invalid-or-partial-after-lock",
                OSError("bootstrap consumption receipt is invalid after lock acquisition"),
            )
        try:
            self._revalidate_root()
            self._validate_lock_artifact(lock_fd)
        except Exception as exc:
            raise self._mark_ambiguous(receipt, "lock-changed-before-receipt-create", exc) from exc
        try:
            fd = os.open(
                self.receipt_path.name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
                dir_fd=self._directory_fd,
            )
        except FileExistsError as exc:
            try:
                existing_status = self._existing_receipt_status()
            except Exception as existing_exc:
                raise self._mark_ambiguous(receipt, "existing-unobservable", existing_exc) from existing_exc
            if isinstance(existing_status, str):
                self._spent_receipt_id = existing_status
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
