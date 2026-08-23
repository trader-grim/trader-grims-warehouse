#!/usr/bin/python3
"""Independent operator verification for TGW Context generation state.

This module deliberately reads root-owned durable state directly.  It does not
call the selected Context MCP process, does not read a provider credential, and
does not authorize or restrict an operator command.  A stale or unavailable
MCP child therefore cannot make an unhealthy fleet look current.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TRANSACTION = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_STATES = {"CURRENT", "UPDATE_PENDING", "RESTART_REQUIRED", "MIXED", "HOLD"}
_MAX_JSON = 8 * 1024 * 1024
_MAX_LEDGER_SEGMENTS = 100_000
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_CURRENT_PLAN_SOURCES = (
    "plan/execution/AMENDMENT-20260823-MCP-LIVE-CLIENT-CONVERGENCE.yaml",
    "pp/PP-ACTOR-MCP-BOUNDARY-001.md",
    "plan/execution/targets/W19-W21-MCP-ONLY-ACTOR-HARDENING-v1.yaml",
    "plan/execution/ACTIVE-PLAN-AMENDMENT-PROCESS-v1.yaml",
)
_INSTRUCTION_ENTRY_POINTS = {
    "claude": "/home/claude/.claude/CLAUDE.md",
    "codex": "/home/codex/.codex/AGENTS.md",
    "deepseek": "/home/deepseek/.dsh/AGENTS.md",
}


class ContextGenerationStatusError(ValueError):
    """The direct status evidence is absent, unsafe, or internally divergent."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


# Dependency-free strict Ed25519 verification.  The installed sudo surface
# runs under the distro Python and never extends its import path with an
# actor-writable virtualenv.
_ED_Q = 2**255 - 19
_ED_L = 2**252 + 27742317777372353535851937790883648493
_ED_D = (-121665 * pow(121666, _ED_Q - 2, _ED_Q)) % _ED_Q
_ED_I = pow(2, (_ED_Q - 1) // 4, _ED_Q)


def _ed_xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_ED_D * y * y + 1, _ED_Q - 2, _ED_Q) % _ED_Q
    x = pow(xx, (_ED_Q + 3) // 8, _ED_Q)
    if (x * x - xx) % _ED_Q:
        x = x * _ED_I % _ED_Q
    if (x * x - xx) % _ED_Q:
        raise ContextGenerationStatusError("Ed25519 point is invalid")
    return x


def _ed_decode(raw: bytes) -> tuple[int, int]:
    if len(raw) != 32:
        raise ContextGenerationStatusError("Ed25519 point length differs")
    encoded = int.from_bytes(raw, "little")
    y = encoded & ((1 << 255) - 1)
    if y >= _ED_Q:
        raise ContextGenerationStatusError("Ed25519 point is non-canonical")
    x = _ed_xrecover(y)
    if (x & 1) != (encoded >> 255):
        x = _ED_Q - x
    if (-x * x + y * y - 1 - _ED_D * x * x * y * y) % _ED_Q:
        raise ContextGenerationStatusError("Ed25519 point is off-curve")
    return x, y


def _ed_add(
    left: tuple[int, int], right: tuple[int, int]
) -> tuple[int, int]:
    x1, y1 = left
    x2, y2 = right
    product = _ED_D * x1 * x2 * y1 * y2 % _ED_Q
    return (
        (x1 * y2 + x2 * y1) * pow(1 + product, _ED_Q - 2, _ED_Q) % _ED_Q,
        (y1 * y2 + x1 * x2) * pow(1 - product, _ED_Q - 2, _ED_Q) % _ED_Q,
    )


def _ed_scalar(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = (0, 1)
    addend = point
    while scalar:
        if scalar & 1:
            result = _ed_add(result, addend)
        addend = _ed_add(addend, addend)
        scalar >>= 1
    return result


_ED_BASE_Y = 4 * pow(5, _ED_Q - 2, _ED_Q) % _ED_Q
_ED_BASE_X = _ed_xrecover(_ED_BASE_Y)
if _ED_BASE_X & 1:
    _ED_BASE_X = _ED_Q - _ED_BASE_X
_ED_BASE = (_ED_BASE_X, _ED_BASE_Y)


def _ed25519_verify(public_key: bytes, signature_text: Any, message: bytes) -> None:
    if not isinstance(signature_text, str):
        raise ContextGenerationStatusError("Ed25519 signature is absent")
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except (TypeError, ValueError) as exc:
        raise ContextGenerationStatusError("Ed25519 signature encoding differs") from exc
    if len(public_key) != 32 or len(signature) != 64:
        raise ContextGenerationStatusError("Ed25519 signature length differs")
    public = _ed_decode(public_key)
    encoded_r = signature[:32]
    point_r = _ed_decode(encoded_r)
    scalar_s = int.from_bytes(signature[32:], "little")
    if scalar_s >= _ED_L:
        raise ContextGenerationStatusError("Ed25519 scalar is non-canonical")
    challenge = int.from_bytes(
        hashlib.sha512(encoded_r + public_key + message).digest(), "little"
    ) % _ED_L
    left = _ed_scalar(_ED_BASE, scalar_s * 8)
    right = _ed_scalar(_ed_add(point_r, _ed_scalar(public, challenge)), 8)
    if left != right:
        raise ContextGenerationStatusError("Ed25519 signature differs")


@dataclass(frozen=True)
class GenerationStatusPaths:
    state_root: Path = Path("/var/lib/tgw/actor-fleet")
    coordinator_transaction_root: Path = Path(
        "/var/lib/tgw/context-update/transactions"
    )
    admission_root: Path = Path("/opt/TGW/tgw-lib/actor-runtime/admissions")
    actor_generation_root: Path = Path(
        "/opt/TGW/tgw-lib/actor-runtime/actor-generations"
    )
    release_root: Path = Path("/opt/TGW/tgw-lib/actor-runtime")
    plan_repository: Path = Path("/opt/TGW/library/plans")
    git: Path = Path("/usr/bin/git")
    actor_public_key: Path = Path(
        "/var/lib/tgw-platform-signers/actor-contract.pub"
    )
    admission_public_key: Path = Path(
        "/var/lib/tgw-platform-signers/release-admission.pub"
    )
    scratch_root: Path = Path("/var/cache/tgw/context-update")

    @property
    def projection(self) -> Path:
        return self.state_root / "fleet-convergence.json"

    @property
    def private_root(self) -> Path:
        return self.state_root / "private"

    @property
    def ledger_root(self) -> Path:
        return self.state_root / "generation-ledger"

    @property
    def ledger_lock(self) -> Path:
        return self.state_root / ".generation-ledger.lock"

    @property
    def active_pointer(self) -> Path:
        return self.state_root / "active-fleet-transaction.json"

    @property
    def supersession_root(self) -> Path:
        return self.state_root / "fleet-supersessions"


def _durable(path: Path, label: str) -> Path:
    if (
        not path.is_absolute()
        or path == Path("/tmp")
        or Path("/tmp") in path.parents
        or path == Path("/opt/TGW/var/tmp")
        or Path("/opt/TGW/var/tmp") in path.parents
    ):
        raise ContextGenerationStatusError(f"{label} is not a durable path")
    return path


def _protected_directory(path: Path, label: str, trusted_uid: int) -> None:
    _durable(path, label)
    current = path
    while True:
        try:
            ancestor = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise ContextGenerationStatusError(
                f"{label} ancestry is unavailable"
            ) from exc
        if (
            current.is_symlink()
            or not stat.S_ISDIR(ancestor.st_mode)
            or ancestor.st_uid not in (
                {trusted_uid} if trusted_uid == 0 else {0, trusted_uid}
            )
            or ancestor.st_mode & 0o022
        ):
            raise ContextGenerationStatusError(f"{label} ancestry is not protected")
        if current == Path("/"):
            break
        current = current.parent
    try:
        observed = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ContextGenerationStatusError(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != trusted_uid
        or observed.st_mode & 0o022
    ):
        raise ContextGenerationStatusError(f"{label} is not protected")


def _exact_private_directory(
    path: Path, label: str, trusted_uid: int, trusted_gid: int,
) -> None:
    _durable(path, label)
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ContextGenerationStatusError(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or metadata.st_gid != trusted_gid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ContextGenerationStatusError(f"{label} is not exact root-private state")


def _private_json(
    path: Path, label: str, trusted_uid: int, trusted_gid: int,
) -> dict[str, Any]:
    value = _protected_json(path, label, trusted_uid)
    metadata = path.stat(follow_symlinks=False)
    if (
        metadata.st_gid != trusted_gid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ContextGenerationStatusError(f"{label} is not exact root-private state")
    return value


def _protected_json(
    path: Path, label: str, trusted_uid: int, *, maximum: int = _MAX_JSON,
) -> dict[str, Any]:
    _durable(path, label)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            before = os.fstat(descriptor)
            chunks: list[bytes] = []
            remaining = maximum + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ContextGenerationStatusError(f"{label} is unavailable") from exc
    raw = b"".join(chunks)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != trusted_uid
        or before.st_nlink != 1
        or before.st_mode & 0o022
        or len(raw) > maximum
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ContextGenerationStatusError(f"{label} is not protected")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextGenerationStatusError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ContextGenerationStatusError(f"{label} is not a JSON object")
    return value


def _protected_bytes(
    path: Path,
    label: str,
    trusted_uid: int,
    *,
    maximum: int = 1024 * 1024,
) -> tuple[bytes, os.stat_result]:
    _durable(path, label)
    _protected_directory(path.parent, f"{label} parent", trusted_uid)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            before = os.fstat(descriptor)
            raw = bytearray()
            while len(raw) <= maximum:
                chunk = os.read(
                    descriptor, min(1024 * 1024, maximum + 1 - len(raw))
                )
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ContextGenerationStatusError(f"{label} is unavailable") from exc
    def identity(value: os.stat_result) -> tuple[int, ...]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_mode,
            value.st_uid,
            value.st_gid,
            value.st_nlink,
        )
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != trusted_uid
        or before.st_nlink != 1
        or before.st_mode & 0o022
        or len(raw) > maximum
        or identity(before) != identity(after)
    ):
        raise ContextGenerationStatusError(f"{label} is not protected")
    return bytes(raw), after


def _verified_hashed_record(
    value: Mapping[str, Any], hash_field: str, label: str,
) -> str:
    unsigned = dict(value)
    claimed = unsigned.pop(hash_field, None)
    if not isinstance(claimed, str) or _HASH.fullmatch(claimed) is None:
        raise ContextGenerationStatusError(f"{label} hash is invalid")
    if claimed != _hash(unsigned):
        raise ContextGenerationStatusError(f"{label} hash differs")
    return claimed


def _verify_ledger(
    paths: GenerationStatusPaths, trusted_uid: int,
) -> tuple[list[dict[str, Any]], str]:
    _protected_directory(paths.ledger_root, "generation ledger root", trusted_uid)
    try:
        lock = os.open(
            paths.ledger_lock,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise ContextGenerationStatusError("generation ledger lock is unavailable") from exc
    entries: list[dict[str, Any]] = []
    try:
        fcntl.flock(lock, fcntl.LOCK_SH)
        segments = sorted(paths.ledger_root.iterdir(), key=lambda item: item.name)
        if not segments or len(segments) > _MAX_LEDGER_SEGMENTS:
            raise ContextGenerationStatusError("generation ledger length is invalid")
        previous: str | None = None
        for sequence, segment in enumerate(segments, 1):
            if not segment.name.endswith(".json"):
                raise ContextGenerationStatusError("generation ledger contains an unknown entry")
            value = _protected_json(
                segment, "generation ledger segment", trusted_uid,
            )
            claimed = _verified_hashed_record(
                value, "record_sha256", "generation ledger segment",
            )
            expected_name = (
                f"{sequence:012d}-{claimed.removeprefix('sha256:')}.json"
            )
            if (
                value.get("schema") != "tgw-generation-ledger-entry/v1"
                or value.get("sequence") != sequence
                or value.get("previous_record_sha256") != previous
                or segment.name != expected_name
            ):
                raise ContextGenerationStatusError("generation ledger chain differs")
            entries.append(value)
            previous = claimed
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
    assert previous is not None
    return entries, previous


def _verify_pointer(
    paths: GenerationStatusPaths, trusted_uid: int,
) -> dict[str, Any] | None:
    if not paths.active_pointer.exists() and not paths.active_pointer.is_symlink():
        return None
    pointer = _protected_json(
        paths.active_pointer, "active fleet pointer", trusted_uid,
    )
    _verified_hashed_record(pointer, "pointer_sha256", "active fleet pointer")
    if (
        pointer.get("schema") != "tgw-active-fleet-transaction/v1"
        or _TRANSACTION.fullmatch(str(pointer.get("transaction_id", ""))) is None
    ):
        raise ContextGenerationStatusError("active fleet pointer is invalid")
    return pointer


def _verify_supersessions(
    paths: GenerationStatusPaths, trusted_uid: int,
) -> dict[str, dict[str, Any]]:
    root = paths.supersession_root
    if not root.exists() and not root.is_symlink():
        return {}
    _protected_directory(root, "fleet supersession root", trusted_uid)
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if not path.name.endswith(".json"):
            raise ContextGenerationStatusError("fleet supersession root has an unknown entry")
        value = _protected_json(path, "fleet supersession", trusted_uid)
        _verified_hashed_record(value, "supersession_sha256", "fleet supersession")
        identity = str(value.get("superseded_transaction_id", ""))
        if (
            value.get("schema") != "tgw-fleet-supersession/v1"
            or _TRANSACTION.fullmatch(identity) is None
            or path.name != f"{identity}.json"
            or identity in result
        ):
            raise ContextGenerationStatusError("fleet supersession is invalid")
        result[identity] = value
    return result


def _verify_coordinator_openings(
    entries: list[dict[str, Any]], paths: GenerationStatusPaths,
    trusted_uid: int, trusted_gid: int,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    verified: list[dict[str, Any]] = []
    internals: dict[str, dict[str, Any]] = {}
    openings = [
        entry for entry in entries
        if entry.get("record_role") == "COORDINATOR_OPENING"
    ]
    if openings:
        _exact_private_directory(
            paths.coordinator_transaction_root,
            "coordinator private transaction root",
            trusted_uid,
            trusted_gid,
        )
    for entry in openings:
        transaction_id = str(entry.get("transaction_id", ""))
        expected = entry.get("coordinator_journal_sha256")
        if (
            _TRANSACTION.fullmatch(transaction_id) is None
            or not isinstance(expected, str)
            or _HASH.fullmatch(expected) is None
        ):
            raise ContextGenerationStatusError("coordinator ledger opening is incomplete")
        transaction_root = paths.coordinator_transaction_root / transaction_id
        _exact_private_directory(
            transaction_root,
            "coordinator private transaction",
            trusted_uid,
            trusted_gid,
        )
        journal = _private_json(
            transaction_root / "private-journal.json",
            "coordinator private journal",
            trusted_uid,
            trusted_gid,
        )
        if _hash(journal) != expected:
            raise ContextGenerationStatusError("coordinator private journal hash differs")
        binding = _private_json(
            transaction_root / "coordinator-binding.json",
            "coordinator private binding",
            trusted_uid,
            trusted_gid,
        )
        binding_hash = _verified_hashed_record(
            binding, "binding_sha256", "coordinator private binding"
        )
        review = entry.get("review_receipt")
        admission = entry.get("admission_receipt")
        if (
            binding.get("outer_transaction_id") != transaction_id
            or binding.get("coordinator_journal_sha256") != expected
            or binding.get("coordinator_ledger_opening_sha256")
            != entry.get("record_sha256")
            or binding.get("effect_plan_sha256")
            != journal.get("effect_plan", {}).get("effect_plan_sha256")
            or not isinstance(review, Mapping)
            or set(review)
            != {"status", "candidate_commit", "solution_hash", "receipt_hash"}
            or review.get("status") != "PASS"
            or review.get("candidate_commit") != entry.get("candidate_commit")
            or review.get("solution_hash")
            != entry.get("approved_plan_solution_hash")
            or review.get("receipt_hash") != entry.get("review_receipt_hash")
            or not isinstance(admission, Mapping)
            or admission.get("receipt_hash") != entry.get("admission_receipt_hash")
        ):
            raise ContextGenerationStatusError("coordinator opening evidence differs")
        verified.append(
            {
                "transaction_id": transaction_id,
                "journal_sha256": expected,
                "binding_sha256": binding_hash,
                "opening_record_sha256": entry["record_sha256"],
            }
        )
        internals[transaction_id] = {
            "journal": journal,
            "binding": binding,
            "opening": entry,
        }
    return verified, internals


def _journal_preimage_bytes(
    journal: Mapping[str, Any], target_id: str, label: str
) -> bytes:
    matches = [
        item for item in journal.get("preimages", [])
        if isinstance(item, Mapping) and item.get("target_id") == target_id
    ]
    if len(matches) != 1 or matches[0].get("kind") != "file":
        raise ContextGenerationStatusError(f"{label} preimage is unavailable")
    payload = matches[0].get("payload")
    if not isinstance(payload, Mapping) or payload.get("encoding") != "base64":
        raise ContextGenerationStatusError(f"{label} preimage differs")
    try:
        raw = base64.b64decode(str(payload.get("content", "")), validate=True)
    except (TypeError, ValueError) as exc:
        raise ContextGenerationStatusError(f"{label} preimage differs") from exc
    if len(raw) != 32:
        raise ContextGenerationStatusError(f"{label} preimage differs")
    return raw


def _transaction_public_keys(
    paths: GenerationStatusPaths,
    internal: Mapping[str, Any],
    *,
    direction: str,
    trusted_uid: int,
) -> tuple[bytes, bytes]:
    journal = internal.get("journal")
    opening = internal.get("opening")
    if not isinstance(journal, Mapping) or not isinstance(opening, Mapping):
        raise ContextGenerationStatusError("coordinator trust binding is unavailable")
    if direction == "successor":
        actor, _actor_state = _protected_bytes(
            paths.actor_public_key, "canonical actor public key", trusted_uid,
            maximum=32,
        )
        admission, _admission_state = _protected_bytes(
            paths.admission_public_key,
            "canonical admission public key",
            trusted_uid,
            maximum=32,
        )
        trust = journal.get("trust_projection")
        if (
            "sha256:" + hashlib.sha256(actor).hexdigest()
            != opening.get("successor_actor_public_sha256")
            or not isinstance(trust, Mapping)
            or trust.get("public_key_sha256", {}).get("release-admission")
            != "sha256:" + hashlib.sha256(admission).hexdigest()
        ):
            raise ContextGenerationStatusError("successor verifier identity differs")
        return actor, admission
    if direction != "predecessor":
        raise ContextGenerationStatusError("fleet transaction direction differs")
    actor = _journal_preimage_bytes(
        journal, "actor-public-trust", "predecessor actor public key"
    )
    admission = _journal_preimage_bytes(
        journal, "admission-public-trust", "predecessor admission public key"
    )
    if (
        "sha256:" + hashlib.sha256(actor).hexdigest()
        != opening.get("predecessor_actor_public_sha256")
    ):
        raise ContextGenerationStatusError("predecessor verifier identity differs")
    return actor, admission


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ContextGenerationStatusError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContextGenerationStatusError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContextGenerationStatusError(f"{label} is not timezone-bound")
    return parsed.astimezone(timezone.utc)


def _verify_active_admission(
    value: Mapping[str, Any],
    *,
    digest: str,
    public_key: bytes,
    revisions: Mapping[str, Any],
    selected: Mapping[str, Any],
    opening: Mapping[str, Any],
) -> None:
    expected_fields = {
        "schema", "request_id", "candidate", "plan", "environment", "status",
        "reasons", "activation", "issued_at", "expires_at", "signer_key_id",
        "receipt_hash", "signature",
    }
    signed = dict(value)
    signature = signed.pop("signature", None)
    unsigned = dict(signed)
    claimed = unsigned.pop("receipt_hash", None)
    issued = _parse_time(value.get("issued_at"), "admission issued_at")
    expires = _parse_time(value.get("expires_at"), "admission expires_at")
    opened = _parse_time(opening.get("recorded_at"), "coordinator opening time")
    if (
        set(value) != expected_fields
        or value.get("schema") != "tgw-w16-release-admission-receipt/v1"
        or claimed != digest
        or claimed != _hash(unsigned)
        or value.get("status") != "ADMITTED"
        or value.get("reasons") != []
        or value.get("activation") != "declarative-only"
        or value.get("signer_key_id") != "tgw-release-admission"
        or value.get("candidate")
        != {"commit": selected.get("commit"), "tree": selected.get("tree")}
        or value.get("plan")
        != {
            "commit": revisions.get("approved_plan"),
            "solution_hash": revisions.get("approved_solution"),
        }
        or not issued <= opened < expires
    ):
        raise ContextGenerationStatusError("active release admission differs")
    _ed25519_verify(public_key, signature, _canonical(signed))


def _verify_admission_references(
    entries: list[dict[str, Any]], paths: GenerationStatusPaths, trusted_uid: int,
    *, active_digest: str | None,
) -> dict[str, list[str]]:
    verified: list[str] = []
    missing: list[str] = []
    for digest in sorted(
        {
            str(entry.get("admission_receipt_hash"))
            for entry in entries
            if _HASH.fullmatch(str(entry.get("admission_receipt_hash", "")))
        }
    ):
        path = paths.admission_root / f"{digest.removeprefix('sha256:')}.json"
        if not path.exists() and not path.is_symlink():
            missing.append(digest)
            continue
        value = _protected_json(path, "release admission receipt", trusted_uid)
        if value.get("receipt_hash") != digest:
            raise ContextGenerationStatusError("release admission receipt identity differs")
        unsigned = dict(value)
        unsigned.pop("signature", None)
        claimed = unsigned.pop("receipt_hash", None)
        if claimed != _hash(unsigned):
            raise ContextGenerationStatusError("release admission receipt hash differs")
        verified.append(digest)
    if active_digest is not None and active_digest not in verified:
        raise ContextGenerationStatusError(
            "active generation admission receipt is absent or unverified"
        )
    return {
        "verified": verified,
        "unverified_external_historical": missing,
    }


def _git(
    paths: GenerationStatusPaths, *arguments: str, accepted: set[int] = {0},
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            str(paths.git),
            "-c", f"safe.directory={paths.plan_repository}",
            "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null",
            "-c", "maintenance.auto=false",
            "-C", str(paths.plan_repository),
            *arguments,
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        env={
            "HOME": "/root",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "XDG_CONFIG_HOME": "/var/empty",
            "TMPDIR": str(paths.scratch_root),
        },
    )
    if result.returncode not in accepted:
        raise ContextGenerationStatusError("canonical Plan Git observation failed")
    return result


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _verify_plan_repository(
    paths: GenerationStatusPaths,
    *,
    approved: str,
    expected_evidence: str,
    expected_tree: str,
    expected_sources: Mapping[str, Any],
) -> dict[str, Any]:
    if any(_COMMIT.fullmatch(value) is None for value in (approved, expected_evidence, expected_tree)):
        raise ContextGenerationStatusError("projected Plan identities are invalid")
    head = _git(paths, "rev-parse", "HEAD^{commit}").stdout.strip()
    tree = _git(paths, "rev-parse", "HEAD^{tree}").stdout.strip()
    dirty = _git(
        paths, "status", "--porcelain=v1", "--untracked-files=all"
    ).stdout
    ancestry = _git(
        paths, "merge-base", "--is-ancestor", approved, head,
        accepted={0, 1},
    ).returncode == 0
    if (
        _COMMIT.fullmatch(head) is None
        or _COMMIT.fullmatch(tree) is None
        or dirty
        or not ancestry
        or set(expected_sources) != set(_CURRENT_PLAN_SOURCES)
    ):
        raise ContextGenerationStatusError("canonical Plan repository is dirty or divergent")
    current_sources: dict[str, str] = {}
    for relative in _CURRENT_PLAN_SOURCES:
        path = paths.plan_repository / relative
        if path.is_symlink() or not path.is_file():
            raise ContextGenerationStatusError("canonical Plan source is unavailable")
        current_sources[relative] = _file_hash(path)
    evidence_current = (
        head == expected_evidence
        and tree == expected_tree
        and current_sources == dict(expected_sources)
    )
    return {
        "approved_commit": approved,
        "expected_evidence_commit": expected_evidence,
        "expected_evidence_tree": expected_tree,
        "observed_evidence_commit": head,
        "observed_evidence_tree": tree,
        "approved_is_ancestor": ancestry,
        "clean": True,
        "current_plan_sources": current_sources,
        "current_plan_sources_sha256": _hash(current_sources),
        "state": "CURRENT" if evidence_current else "UPDATE_PENDING",
    }


def _verify_actor_generation(
    paths: GenerationStatusPaths,
    generation: str,
    trusted_uid: int,
) -> dict[str, Any]:
    if _HASH.fullmatch(generation) is None:
        raise ContextGenerationStatusError("active actor generation identity is invalid")
    root = paths.actor_generation_root / generation.removeprefix("sha256:")
    _protected_directory(root, "active actor generation root", trusted_uid)
    receipt = _protected_json(
        root / "generation-receipt.json",
        "active actor generation receipt",
        trusted_uid,
    )
    claimed = _verified_hashed_record(
        receipt, "receipt_hash", "active actor generation receipt"
    )
    if receipt.get("generation") != generation or receipt.get("status") != "PREPARED":
        raise ContextGenerationStatusError("active actor generation receipt differs")
    return {"generation": generation, "receipt_hash": claimed}


def _verify_selected_release(
    paths: GenerationStatusPaths,
    revisions: Mapping[str, Any],
    selected_projection: Any,
    trusted_uid: int,
) -> dict[str, Any]:
    current = paths.release_root / "current"
    if not current.is_symlink():
        raise ContextGenerationStatusError("selected release pointer is unavailable")
    target = PurePosixPath(os.readlink(current))
    if target.is_absolute() or len(target.parts) != 2 or target.parts[0] != "releases":
        raise ContextGenerationStatusError("selected release pointer is unsafe")
    release = current.resolve(strict=True)
    if release.parent != (paths.release_root / "releases").resolve(strict=True):
        raise ContextGenerationStatusError("selected release pointer escapes")
    _protected_directory(release, "selected release", trusted_uid)
    manifest = _protected_json(
        release / ".release-manifest.json", "selected release manifest", trusted_uid
    )
    manifest_hash = _hash(manifest)
    if (
        manifest.get("commit") != revisions.get("source_commit")
        or manifest.get("git_tree") != revisions.get("source_tree")
    ):
        raise ContextGenerationStatusError("selected release source differs")
    if not isinstance(selected_projection, Mapping):
        raise ContextGenerationStatusError("active selected release projection is absent")
    if any(
        selected_projection.get(name) != expected for name, expected in (
            ("commit", manifest.get("commit")),
            ("tree", manifest.get("git_tree")),
            ("manifest_sha256", manifest_hash),
        )
    ) or selected_projection.get("path") != str(release) or selected_projection.get(
        "generation"
    ) != target.parts[1]:
        raise ContextGenerationStatusError("selected release projection differs")
    return {
        "generation": target.parts[1],
        "commit": manifest["commit"],
        "tree": manifest["git_tree"],
        "manifest_sha256": manifest_hash,
    }


def verify_generation_status(
    paths: GenerationStatusPaths = GenerationStatusPaths(), *, trusted_uid: int = 0,
    trusted_gid: int = 0,
) -> dict[str, Any]:
    """Verify and return one bounded direct status object.

    ``trusted_uid`` is injectable only for filesystem fixtures; the installed
    command always uses UID 0.
    """
    _protected_directory(paths.state_root, "actor fleet state root", trusted_uid)
    entries, ledger_head = _verify_ledger(paths, trusted_uid)
    pointer = _verify_pointer(paths, trusted_uid)
    supersessions = _verify_supersessions(paths, trusted_uid)
    projection = _protected_json(
        paths.projection, "fleet convergence projection", trusted_uid,
    )
    projection_hash = _verified_hashed_record(
        projection, "projection_sha256", "fleet convergence projection",
    )
    state = projection.get("generation_status")
    if (
        projection.get("schema") != "tgw-fleet-convergence-set/v1"
        or state not in _STATES
        or projection.get("state") not in {
            "ACTIVE", "TERMINAL", "AMBIGUOUS", "NO_TRANSACTION",
        }
        or projection.get("supersessions_sha256") != _hash(supersessions)
    ):
        raise ContextGenerationStatusError("fleet convergence projection is invalid")
    pointer_hash = pointer.get("pointer_sha256") if pointer else None
    if projection.get("active_pointer_sha256") != pointer_hash:
        raise ContextGenerationStatusError("fleet convergence pointer projection differs")

    transaction = projection.get("transaction")
    transaction_id = "none"
    generation = approved = evidence = source = "none"
    pending = 0
    revisions: Mapping[str, Any] = {}
    plan_status: dict[str, Any] | None = None
    actor_generation_status: dict[str, Any] | None = None
    selected_release_status: dict[str, Any] | None = None
    active_admission: str | None = None
    active_review: str | None = None
    evidence_health = "NOT_APPLICABLE"
    instruction_status: dict[str, dict[str, str]] = {}
    if isinstance(transaction, Mapping):
        inner_hash = _verified_hashed_record(
            transaction, "projection_sha256", "fleet transaction projection",
        )
        transaction_id = str(transaction.get("transaction_id", ""))
        if _TRANSACTION.fullmatch(transaction_id) is None:
            raise ContextGenerationStatusError("fleet transaction identity is invalid")
        journal = _protected_json(
            paths.private_root / f"{transaction_id}.actor-provider.json",
            "actor provider journal",
            trusted_uid,
        )
        if transaction.get("journal_sha256") != _hash(journal):
            raise ContextGenerationStatusError("fleet transaction journal projection differs")
        sequence = transaction.get("ledger_sequence")
        if (
            not isinstance(sequence, int)
            or not 1 <= sequence <= len(entries)
            or entries[sequence - 1].get("record_sha256")
            != transaction.get("ledger_record_sha256")
        ):
            raise ContextGenerationStatusError("fleet transaction ledger projection differs")
        revisions = transaction.get("target_revisions")
        required_revisions = {
            "approved_plan", "approved_solution", "evidence_plan", "evidence_tree",
            "source_commit", "source_tree", "current_plan_sources",
            "current_plan_sources_sha256", "catalog", "bootstrap", "broker_policy",
            "review", "admission",
        }
        if not isinstance(revisions, Mapping) or set(revisions) != required_revisions:
            raise ContextGenerationStatusError("fleet projected revisions are incomplete")
        if revisions["current_plan_sources_sha256"] != _hash(revisions["current_plan_sources"]):
            raise ContextGenerationStatusError("fleet projected Plan source aggregate differs")
        approved = str(revisions["approved_plan"])
        evidence = str(revisions["evidence_plan"])
        source = str(revisions["source_commit"])
        active_admission = str(revisions["admission"])
        active_review = str(revisions["review"])
        if any(
            _HASH.fullmatch(str(revisions[name])) is None
            for name in ("approved_solution", "catalog", "bootstrap", "broker_policy", "review", "admission")
        ):
            raise ContextGenerationStatusError("fleet projected content identities differ")
        target_generation = str(transaction.get("target_generation", "none"))
        generation = target_generation.removeprefix("sha256:")[:12]
        plan_status = _verify_plan_repository(
            paths,
            approved=approved,
            expected_evidence=evidence,
            expected_tree=str(revisions["evidence_tree"]),
            expected_sources=revisions["current_plan_sources"],
        )
        actor_generation_status = _verify_actor_generation(
            paths, target_generation, trusted_uid
        )
        selected_release_status = _verify_selected_release(
            paths,
            revisions,
            transaction.get("selected_release"),
            trusted_uid,
        )
        obligations = transaction.get("obligations", [])
        global_pending = transaction.get("global_pending", [])
        if not isinstance(obligations, list) or not isinstance(global_pending, list):
            raise ContextGenerationStatusError("fleet pending projection is invalid")
        pending = len(global_pending) + sum(
            len(item.get("pending_reasons", []))
            for item in obligations if isinstance(item, Mapping)
        )
        transaction_projection_hash = inner_hash
        actor_verifications = transaction.get("actor_verifications")
        if (
            not isinstance(actor_verifications, list)
            or state == "CURRENT" and (
                not actor_verifications
                or {str(item.get("actor")) for item in actor_verifications if isinstance(item, Mapping)}
                != set(transaction.get("actors", []))
                or any(
                    _HASH.fullmatch(str(item.get("primary_real_store_semantic_sha256", ""))) is None
                    or _HASH.fullmatch(str(item.get("instruction_entry_point_sha256", ""))) is None
                    or item.get("instruction_entry_point_path")
                    != _INSTRUCTION_ENTRY_POINTS.get(str(item.get("actor")))
                    for item in actor_verifications if isinstance(item, Mapping)
                )
            )
        ):
            raise ContextGenerationStatusError("active real-store verification is incomplete")
        admission_evidence = transaction.get("admission_evidence")
        real_store_rows = [
            {
                "actor": item.get("actor"),
                "semantic_sha256": item.get("primary_real_store_semantic_sha256"),
                "instruction_path": item.get("instruction_entry_point_path"),
                "instruction_sha256": item.get("instruction_entry_point_sha256"),
                "proof_sha256": item.get("actor_proof_hash"),
            }
            for item in actor_verifications if isinstance(item, Mapping)
        ]
        cold_hashes = sorted(
            str(item.get("client_confirmation_hash"))
            for item in obligations
            if isinstance(item, Mapping)
            and _HASH.fullmatch(str(item.get("client_confirmation_hash", "")))
        )
        if state == "CURRENT" and (
            not isinstance(admission_evidence, Mapping)
            or admission_evidence.get("admission_receipt_sha256") != active_admission
            or admission_evidence.get("review_receipt_sha256") != active_review
            or transaction.get("real_store_evidence_sha256")
            != _hash(sorted(real_store_rows, key=lambda item: str(item["actor"])))
            or transaction.get("cold_handoff_evidence_sha256") != _hash(cold_hashes)
            or transaction.get("confinement_state")
            != "NON_CONFINING_ACTOR_COMPOSITE_STORES"
            or _HASH.fullmatch(str(transaction.get("journal_payload_sha256", ""))) is None
            or _HASH.fullmatch(str(transaction.get("coordinator_binding_sha256", ""))) is None
        ):
            raise ContextGenerationStatusError("active admission or cold-handoff evidence is incomplete")
        instruction_status = {
            str(item["actor"]): {
                "path": str(item["instruction_entry_point_path"]),
                "sha256": str(item["instruction_entry_point_sha256"]),
            }
            for item in actor_verifications
            if isinstance(item, Mapping)
            and item.get("actor") in _INSTRUCTION_ENTRY_POINTS
        }
        evidence_health = "VERIFIED"
    elif projection.get("state") in {"ACTIVE", "TERMINAL"}:
        raise ContextGenerationStatusError("fleet transaction projection is absent")
    else:
        transaction_projection_hash = None

    coordinator_openings = _verify_coordinator_openings(
        entries, paths, trusted_uid, trusted_gid,
    )
    admissions = _verify_admission_references(
        entries, paths, trusted_uid, active_digest=active_admission,
    )
    aggregate = state
    if plan_status is not None and plan_status["state"] == "UPDATE_PENDING" and state == "CURRENT":
        aggregate = "UPDATE_PENDING"
    instruction_status_sha256 = _hash(instruction_status)
    line = (
        f"TGW Context generation: client=INDEPENDENT fleet={state} "
        f"aggregate={aggregate} evidence_health={evidence_health} gen={generation} "
        f"approved={approved[:12]} evidence={evidence[:12]} "
        f"plan_head={(plan_status or {}).get('observed_evidence_commit', 'none')[:12]} "
        f"source={source[:12]} "
        f"instructions={instruction_status_sha256.removeprefix('sha256:')[:12]} "
        f"tx={transaction_id} pending={pending} ledger={len(entries)}:"
        f"{ledger_head.removeprefix('sha256:')[:12]}"
    )
    return {
        "schema": "tgw-context-generation-direct-status/v1",
        "status": aggregate,
        "client_state": "INDEPENDENT",
        "fleet_state": state,
        "evidence_health": evidence_health,
        "line": line,
        "projection_sha256": projection_hash,
        "transaction_projection_sha256": transaction_projection_hash,
        "ledger": {
            "entries": len(entries),
            "head_record_sha256": ledger_head,
            "coordinator_openings": coordinator_openings,
            "verified_admission_receipts": admissions["verified"],
            "unverified_external_historical_admissions": admissions[
                "unverified_external_historical"
            ],
        },
        "plan": plan_status,
        "actor_generation": actor_generation_status,
        "actor_instructions": instruction_status,
        "actor_instructions_sha256": instruction_status_sha256,
        "selected_release": selected_release_status,
        "active_review_receipt_hash": active_review,
        "active_admission_receipt_hash": active_admission,
        "active_pointer_sha256": pointer_hash,
        "supersessions_sha256": _hash(supersessions),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-context-generation-status")
    parser.add_argument(
        "--json", action="store_true",
        help="emit bounded verified detail instead of only the concise line",
    )
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ContextGenerationStatusError(
                "run the installed read-only verifier with sudo -n"
            )
        result = verify_generation_status()
    except (OSError, TypeError, ValueError, ContextGenerationStatusError) as exc:
        result = {
            "schema": "tgw-context-generation-direct-status/v1",
            "status": "HOLD",
            "client_state": "INDEPENDENT",
            "fleet_state": "HOLD",
            "line": f"TGW Context generation: client=INDEPENDENT fleet=HOLD aggregate=HOLD error={exc}",
        }
        print(json.dumps(result, sort_keys=True) if args.json else result["line"])
        return 2
    print(json.dumps(result, sort_keys=True) if args.json else result["line"])
    # A valid non-CURRENT state is information, never an operator authority
    # gate.  Only an unreadable/corrupt verifier path exits nonzero above.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
