"""Durable, evidence-bound supervision for the local coding lifecycle.

The journal is a coordinator and recovery index.  It never accepts an untyped
command, grants authority, or turns a claim into evidence.  Existing queues,
receipts, Git integration, Doctor checks, and the current-task publisher remain
the owners of their respective effects; this module binds their immutable
results to one Todo/PP root and makes restart replay deterministic.
"""

from __future__ import annotations

import fcntl
import grp
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA = "tgw-local-coding-lifecycle/v2"
BINDING_SCHEMA = "tgw-local-coding-lifecycle-binding/v2"
JOB_BINDING_SCHEMA = "tgw-local-coding-lifecycle-job-binding/v2"
STAGES = (
    "implementation",
    "controller",
    "candidate",
    "review",
    "integration",
    "materialization",
    "live_verification",
    "terminal_publication",
    "operator_notification",
)
TYPED_STAGE_IMPLEMENTATIONS = {
    "implementation": "coding-queue:codex-implement/v1",
    "controller": "coding-queue:controller-verify/v1",
    "candidate": "coding-lineage:closed-candidate/v1",
    "review": "coding-queue:claude-review/v1",
    "integration": "coding-git:fast-forward/v1",
    "materialization": "doctor-receipt:runtime-materialization/v1",
    "live_verification": "doctor-receipt:runtime/v1",
    "terminal_publication": "current-task-receipt:context/v1",
    "operator_notification": "lifecycle-journal:notification/v1",
}
TERMINAL = frozenset(
    {"TECHNICALLY_COMPLETE", "SUCCEEDED", "FAILED", "REMEDIATION_REQUIRED"}
)
FAILURE_OUTCOMES = frozenset({"failed", "remediation", "resumable_partial"})
BOUNDARIES = {
    "ssh": False,
    "sudo": False,
    "tgw_prod": False,
    "remote_provision": False,
    "remote_provision_api": False,
    "hidden_approval": False,
    "approval_card": False,
    "admission": False,
    "context_authority": False,
    "context_dispatch": False,
    "actor_fleet": False,
    "memory": False,
    "business_provider_effect": False,
    "production_effect": False,
}

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_ROOT = re.compile(r"coding:[0-9a-f]{64}\Z")


class LifecycleError(RuntimeError):
    """A lifecycle binding or journal is unsafe."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root_id(target: int | str, _plan_commit: str | None = None,
            _source_commit: str | None = None) -> str:
    """Return the one durable identity for a selected Todo/PP.

    Plan and source generations belong in the validated binding, not the path.
    A changed source must therefore report stale remediation on the same root
    instead of silently creating an ambiguous second lifecycle.
    """

    value = {"schema": "tgw-local-coding-root/v2", "target": str(target)}
    return "coding:" + hashlib.sha256(_canonical(value)).hexdigest()


def build_binding(
    *,
    target: int | str,
    plan_binding: Mapping[str, Any],
    source_tree: str,
    boundaries: Mapping[str, bool] = BOUNDARIES,
) -> dict[str, Any]:
    """Validate and persist the complete Plan/Todo execution-card binding."""

    from tgw.development.plan_binding import (
        MalformedPlanBindingError,
        validate_plan_binding,
    )

    todo_id = int(target) if str(target).isdigit() else None
    try:
        plan = validate_plan_binding(plan_binding, todo_id=todo_id)
    except (MalformedPlanBindingError, TypeError, ValueError) as exc:
        raise LifecycleError(f"coding lifecycle Plan/Todo binding is invalid: {exc}") from exc
    if not _COMMIT.fullmatch(source_tree):
        raise LifecycleError("coding lifecycle source tree is invalid")
    if (
        _COMMIT.fullmatch(plan["plan_commit"]) is None
        or _COMMIT.fullmatch(plan["source_commit"]) is None
        or _SHA256.fullmatch(plan["solution_hash"]) is None
        or not Path(plan["worktree"]).is_absolute()
        or plan["worktree_identity"].get("worktree") != plan["worktree"]
    ):
        raise LifecycleError(
            "coding lifecycle Plan/source/worktree identity is incomplete"
        )
    if dict(boundaries) != BOUNDARIES:
        raise LifecycleError("coding lifecycle authority/dependency boundaries differ")
    root = plan["execution_root"]
    if root["kind"] == "todo" and root["todo_id"] != todo_id:
        raise LifecycleError("coding lifecycle Todo root differs from its target")
    if root["kind"] == "pp" and root["pp_ref"] != str(target):
        raise LifecycleError("coding lifecycle PP root differs from its target")
    if not _SHA256.fullmatch(plan["closure_hash"]):
        raise LifecycleError("coding lifecycle closure hash is invalid")
    if not _SHA256.fullmatch(plan["idempotency_key"]):
        raise LifecycleError("coding lifecycle card idempotency key is invalid")
    unsigned = {
        "schema": BINDING_SCHEMA,
        "target": str(target),
        "plan_commit": plan["plan_commit"],
        "solution_hash": plan["solution_hash"],
        "closure_hash": plan["closure_hash"],
        "capability": plan["capability"],
        "treatment_id": plan["treatment_id"],
        "source_commit": plan["source_commit"],
        "source_tree": source_tree,
        "execution_root": root,
        "execution_root_identity": root["identity_hash"],
        "card_idempotency_key": plan["idempotency_key"],
        "worktree": plan["worktree"],
        "worktree_identity": plan["worktree_identity"],
        "plan_todo_binding": plan,
        "boundaries": dict(boundaries),
    }
    return {**unsigned, "binding_hash": _hash(unsigned)}


def validate_binding(value: object, *, target: int | str | None = None) -> dict[str, Any]:
    """Validate a complete stored lifecycle binding without filling defaults."""

    if not isinstance(value, Mapping) or value.get("schema") != BINDING_SCHEMA:
        raise LifecycleError("coding lifecycle binding schema is invalid")
    binding = dict(value)
    claimed = binding.pop("binding_hash", None)
    if claimed != _hash(binding):
        raise LifecycleError("coding lifecycle binding hash mismatch")
    if target is not None and binding.get("target") != str(target):
        raise LifecycleError("coding lifecycle target binding mismatch")
    rebuilt = build_binding(
        target=binding.get("target", ""),
        plan_binding=binding.get("plan_todo_binding", {}),
        source_tree=str(binding.get("source_tree", "")),
        boundaries=binding.get("boundaries", {}),
    )
    if rebuilt != value:
        raise LifecycleError("coding lifecycle binding fields are contradictory")
    return dict(value)


def job_binding(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact immutable lifecycle identity every queue job must carry."""

    binding = validate_binding(record.get("binding"), target=record.get("target"))
    unsigned = {
        "schema": JOB_BINDING_SCHEMA,
        "root_id": record["root_id"],
        "binding_hash": binding["binding_hash"],
        "plan_binding_hash": _hash(binding["plan_todo_binding"]),
        "execution_root_identity": binding["execution_root_identity"],
        "card_idempotency_key": binding["card_idempotency_key"],
        "closure_hash": binding["closure_hash"],
        # A remediation generation supersedes an earlier resume generation.
        # Prefer its hash even when old journals still carry the archived
        # resume intent so queue deduplication cannot reuse a terminal resume.
        "resume_intent_hash": (
            record.get("remediation_intent", {}).get(
                "remediation_intent_hash"
            )
            or record.get("resume_intent", {}).get("resume_intent_hash")
            or record.get("active_implementation_generation", {}).get(
                "intent_hash"
            )
        ),
    }
    return {**unsigned, "job_binding_hash": _hash(unsigned)}


def _bind_active_implementation_generation(
    record: dict[str, Any], result: Mapping[str, Any]
) -> None:
    """Consume a dispatched intent without changing its immutable job fence.

    A live resume/remediation intent is a one-shot dispatch request. Retaining
    it after the implementation job is durably visible makes a later partial
    look like the same request. The active generation keeps the exact hash and
    request needed for crash replay while allowing the next RESUMABLE_PARTIAL
    transition to mint a new fence.
    """

    jobs = result.get("job_ids")
    if not isinstance(jobs, list) or not jobs:
        return
    live: list[tuple[str, str, Mapping[str, Any]]] = []
    remediation = record.get("remediation_intent")
    if isinstance(remediation, Mapping):
        live.append(
            ("remediation", "remediation_intent_hash", remediation)
        )
    resume = record.get("resume_intent")
    if isinstance(resume, Mapping):
        live.append(("resume", "resume_intent_hash", resume))
    if not live:
        return
    if len(live) != 1:
        raise LifecycleError("coding implementation has conflicting live intents")
    kind, hash_key, intent = live[0]
    intent_hash = intent.get(hash_key)
    if _SHA256.fullmatch(str(intent_hash or "")) is None:
        raise LifecycleError("coding implementation intent hash is invalid")
    before = job_binding(record)
    active = {
        "schema": "tgw-local-coding-active-implementation-generation/v1",
        "kind": kind,
        "intent_hash": intent_hash,
        "intent": dict(intent),
        "job_ids": list(jobs),
        "bound_at": _now(),
    }
    prior = record.get("active_implementation_generation")
    if prior is not None and prior != active:
        raise LifecycleError("coding implementation generation changed while active")
    record["active_implementation_generation"] = active
    record.pop(f"{kind}_intent", None)
    if job_binding(record) != before:
        raise LifecycleError("consuming coding intent changed the active job fence")


def validate_job_binding(record: Mapping[str, Any], value: object) -> dict[str, Any]:
    expected = job_binding(record)
    if not isinstance(value, Mapping) or dict(value) != expected:
        raise LifecycleError("coding job lifecycle binding is absent or stale")
    return expected


def validate_job_binding_payload(
    value: object, *, plan_binding: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate a queue-carried lifecycle fence without journal access.

    Workers deliberately do not acquire lifecycle authority.  They can still
    prove that the immutable job fence is self-hashed and repeats the exact
    execution-root/card/closure identities from the already-validated Todo
    card.  The supervisor later compares the entire value to its journal.
    """

    if not isinstance(value, Mapping):
        raise LifecycleError("coding job lifecycle binding is absent")
    result = dict(value)
    unsigned = {key: item for key, item in result.items() if key != "job_binding_hash"}
    if (
        set(result) != {
            "schema",
            "root_id",
            "binding_hash",
            "plan_binding_hash",
            "execution_root_identity",
            "card_idempotency_key",
            "closure_hash",
            "resume_intent_hash",
            "job_binding_hash",
        }
        or result.get("schema") != JOB_BINDING_SCHEMA
        or _ROOT.fullmatch(str(result.get("root_id", ""))) is None
        or _SHA256.fullmatch(str(result.get("binding_hash", ""))) is None
        or result.get("plan_binding_hash") != _hash(plan_binding)
        or result.get("job_binding_hash") != _hash(unsigned)
        or result.get("execution_root_identity")
        != plan_binding.get("execution_root", {}).get("identity_hash")
        or result.get("card_idempotency_key") != plan_binding.get("idempotency_key")
        or result.get("closure_hash") != plan_binding.get("closure_hash")
        or (
            result.get("resume_intent_hash") is not None
            and _SHA256.fullmatch(str(result.get("resume_intent_hash"))) is None
        )
    ):
        raise LifecycleError("coding job lifecycle binding is malformed or stale")
    return result


def candidate_job_binding(
    lifecycle_binding: Mapping[str, Any], *, commit: str, tree: str
) -> dict[str, Any]:
    """Bind review/integration work to one exact closed candidate."""

    if _COMMIT.fullmatch(commit) is None or _COMMIT.fullmatch(tree) is None:
        raise LifecycleError("coding lifecycle candidate identity is invalid")
    unsigned = {
        "schema": "tgw-local-coding-lifecycle-candidate-binding/v1",
        "root_id": lifecycle_binding.get("root_id"),
        "job_binding_hash": lifecycle_binding.get("job_binding_hash"),
        "commit": commit,
        "tree": tree,
    }
    return {**unsigned, "candidate_binding_hash": _hash(unsigned)}


def validate_candidate_job_binding(
    value: object,
    *,
    lifecycle_binding: Mapping[str, Any],
    commit: str,
    tree: str,
) -> dict[str, Any]:
    expected = candidate_job_binding(
        lifecycle_binding, commit=commit, tree=tree
    )
    if not isinstance(value, Mapping) or dict(value) != expected:
        raise LifecycleError("coding lifecycle candidate binding is absent or stale")
    return expected


class LifecycleStore:
    """Atomic, group-shared JSON journals suitable for host restart recovery."""

    def __init__(self, root: Path | str, *, group_name: str = "tgw-coders",
                 group_gid: int | None = None):
        self.root = Path(root)
        self.group_name = group_name
        self.group_gid = group_gid

    def _gid(self) -> int:
        if self.group_gid is not None:
            return self.group_gid
        try:
            return grp.getgrnam(self.group_name).gr_gid
        except KeyError as exc:
            raise LifecycleError(f"coding lifecycle group {self.group_name} is unavailable") from exc

    def _prepare_root(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o2770)
            descriptor = os.open(
                self.root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
            try:
                info = os.fstat(descriptor)
                if info.st_uid == os.geteuid():
                    os.fchown(descriptor, -1, self._gid())
                    os.fchmod(descriptor, 0o2770)
                    info = os.fstat(descriptor)
                if (
                    info.st_gid != self._gid()
                    or stat.S_IMODE(info.st_mode) != 0o2770
                ):
                    raise LifecycleError(
                        "coding lifecycle shared root ownership/mode is unsafe"
                    )
            finally:
                os.close(descriptor)
        except LifecycleError:
            raise
        except OSError as exc:
            raise LifecycleError("coding lifecycle shared root is unavailable") from exc

    def path(self, identity: str) -> Path:
        if _ROOT.fullmatch(identity) is None:
            raise LifecycleError("invalid coding lifecycle root ID")
        return self.root / (identity.removeprefix("coding:") + ".json")

    def _read(self, path: Path) -> dict[str, Any]:
        descriptor = -1
        try:
            descriptor = os.open(
                path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_gid != self._gid()
                or stat.S_IMODE(info.st_mode) != 0o660
            ):
                raise LifecycleError("coding lifecycle journal ownership/mode is unsafe")
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                value = json.load(stream)
        except LifecycleError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LifecycleError("coding lifecycle journal is unavailable or invalid") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(value, dict):
            raise LifecycleError("coding lifecycle journal is unavailable or invalid")
        claimed = value.get("record_hash")
        unsigned = {key: item for key, item in value.items() if key != "record_hash"}
        if claimed != _hash(unsigned) or value.get("schema") != SCHEMA:
            raise LifecycleError("coding lifecycle journal hash/schema mismatch")
        validate_binding(value.get("binding"), target=value.get("target"))
        if value.get("root_id") != root_id(value.get("target", "")):
            raise LifecycleError("coding lifecycle journal root binding mismatch")
        return value

    def get(self, identity: str) -> dict[str, Any] | None:
        path = self.path(identity)
        return None if not path.exists() else self._read(path)

    def put(self, value: Mapping[str, Any]) -> dict[str, Any]:
        identity = str(value.get("root_id", ""))
        path = self.path(identity)
        self._prepare_root()
        unsigned = {key: item for key, item in dict(value).items() if key != "record_hash"}
        document = {**unsigned, "record_hash": _hash(unsigned)}
        descriptor, temporary = tempfile.mkstemp(
            prefix=".coding-lifecycle-", dir=self.root
        )
        try:
            os.fchown(descriptor, -1, self._gid())
            os.fchmod(descriptor, 0o660)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(document, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise LifecycleError("coding lifecycle journal publication failed") from exc
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return document

    def _open_lock(self, identity: str, suffix: str):
        self.path(identity)
        self._prepare_root()
        path = self.root / (identity.removeprefix("coding:") + suffix)
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = -1
        try:
            descriptor = os.open(path, flags, 0o660)
            info = os.fstat(descriptor)
            if info.st_uid == os.geteuid():
                os.fchown(descriptor, -1, self._gid())
                os.fchmod(descriptor, 0o660)
                info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_gid != self._gid()
                or stat.S_IMODE(info.st_mode) != 0o660
            ):
                raise LifecycleError("coding lifecycle lock ownership/mode is unsafe")
            return os.fdopen(descriptor, "a+")
        except LifecycleError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise LifecycleError("coding lifecycle lock is unavailable") from exc

    def locked(self, identity: str):
        lock = self._open_lock(identity, ".lock")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return lock

    def records(self) -> list[dict[str, Any]]:
        """Return every valid lifecycle record; one bad journal fails closed."""

        if not self.root.exists():
            return []
        self._prepare_root()
        return [self._read(path) for path in sorted(self.root.glob("*.json"))]

    def find(self, target: int | str) -> dict[str, Any] | None:
        """Resolve a target to its one stable root, refusing legacy ambiguity."""

        direct = self.get(root_id(target))
        if direct is not None:
            return direct
        matches = [
            record
            for record in self.records()
            if record.get("target") == str(target)
            or record.get("binding", {}).get("target") == str(target)
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise LifecycleError(
                f"coding lifecycle target {target} has conflicting legacy roots"
            )
        return matches[0]

    def supervisor_lock(self, identity: str, *, blocking: bool = False):
        """Claim the one long-lived supervisor slot for a root."""

        lock = self._open_lock(identity, ".supervisor.lock")
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(lock.fileno(), flags)
        except BlockingIOError:
            lock.close()
            return None
        return lock


def create(store: LifecycleStore, *, target: int | str,
           binding: Mapping[str, Any]) -> dict[str, Any]:
    """Create or reuse the sole root and surface changed bindings as remediation."""

    validated = validate_binding(binding, target=target)
    identity = root_id(target)
    lock = store.locked(identity)
    try:
        prior = store.get(identity)
        if prior is not None:
            if prior.get("binding") == validated:
                return prior
            now = _now()
            prior["state"] = "REMEDIATION_REQUIRED"
            prior["failure"] = {
                "stage": prior.get("stage"),
                "reason": "stale lifecycle binding; existing root retained; zero effects",
                "existing_binding_hash": prior.get("binding", {}).get("binding_hash"),
                "observed_binding_hash": validated.get("binding_hash"),
            }
            prior["stale_binding"] = validated
            prior["updated_at"] = now
            return store.put(prior)
        now = _now()
        return store.put(
            {
                "schema": SCHEMA,
                "root_id": identity,
                "target": str(target),
                "binding": validated,
                "boundaries": dict(BOUNDARIES),
                "state": "QUEUED",
                "stage": STAGES[0],
                "created_at": now,
                "updated_at": now,
                "stages": {},
                "effects": {},
                "job_ids": [],
                "publication": {
                    "attempted": False,
                    "attempts": 0,
                    "pending": False,
                    "published": False,
                    "retry_available": True,
                    "next_retry_at": None,
                },
                "operator": {
                    "notification": None,
                    "readback": None,
                    "acceptance": "PENDING",
                },
                "operator_acceptance": "PENDING",
            }
        )
    finally:
        lock.close()


def report_stale_source(
    store: LifecycleStore,
    identity: str,
    *,
    source_commit: str,
    source_tree: str,
) -> dict[str, Any]:
    """Retain the one root while durably reporting source advancement."""

    if _COMMIT.fullmatch(source_commit) is None or _COMMIT.fullmatch(source_tree) is None:
        raise LifecycleError("observed stale source identity is invalid")
    lock = store.locked(identity)
    try:
        record = store.get(identity)
        if record is None:
            raise LifecycleError("coding lifecycle does not exist")
        record["state"] = "REMEDIATION_REQUIRED"
        record["failure"] = {
            "stage": record.get("stage"),
            "reason": "stale lifecycle source; existing root retained; zero effects",
            "bound_source_commit": record["binding"]["source_commit"],
            "bound_source_tree": record["binding"]["source_tree"],
            "observed_source_commit": source_commit,
            "observed_source_tree": source_tree,
        }
        record["updated_at"] = _now()
        return store.put(record)
    finally:
        lock.close()


StageHandler = Callable[[dict[str, Any]], Mapping[str, Any]]


def stage_idempotency_key(record: Mapping[str, Any], stage: str) -> str:
    """Stable effect identity shared by every replay of one bound stage."""

    if stage not in STAGES:
        raise LifecycleError("coding lifecycle stage is invalid")
    binding = validate_binding(record.get("binding"), target=record.get("target"))
    return _hash(
        {
            "root_id": record["root_id"],
            "stage": stage,
            "binding_hash": binding["binding_hash"],
        }
    )


def stage_result(
    record: Mapping[str, Any],
    stage: str,
    outcome: str,
    *,
    receipt: Mapping[str, Any] | None = None,
    reason: str | None = None,
    job_ids: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build the exact result envelope accepted by :func:`advance`."""

    result: dict[str, Any] = {
        "outcome": outcome,
        "idempotency_key": stage_idempotency_key(record, stage),
    }
    if receipt is not None:
        result["receipt"] = dict(receipt)
        result["receipt_hash"] = _hash(receipt)
    if reason is not None:
        result["reason"] = reason
    if job_ids:
        result["job_ids"] = list(job_ids)
    return result


def validate_stage_result(
    record: Mapping[str, Any], stage: str, value: Mapping[str, Any]
) -> dict[str, Any]:
    """Reject claims not bound to an exact immutable receipt and replay key."""

    if not isinstance(value, Mapping):
        raise LifecycleError(f"coding lifecycle stage {stage} returned no result")
    result = dict(value)
    outcome = result.get("outcome")
    allowed = {
        "satisfied",
        "waiting",
        "publication_unavailable",
        *FAILURE_OUTCOMES,
    }
    if outcome not in allowed:
        raise LifecycleError(
            f"coding lifecycle stage {stage} returned an invalid outcome"
        )
    expected = stage_idempotency_key(record, stage)
    if result.get("idempotency_key") != expected:
        raise LifecycleError(
            f"coding lifecycle stage {stage} receipt binding mismatch"
        )
    receipt = result.get("receipt")
    receipt_hash = result.get("receipt_hash")
    if receipt is not None:
        if not isinstance(receipt, Mapping) or receipt_hash != _hash(receipt):
            raise LifecycleError(
                f"coding lifecycle stage {stage} immutable receipt hash mismatch"
            )
    elif receipt_hash is not None:
        raise LifecycleError(
            f"coding lifecycle stage {stage} receipt hash has no receipt"
        )
    if outcome == "satisfied" and receipt is None:
        raise LifecycleError(
            f"coding lifecycle stage {stage} cannot be satisfied without evidence"
        )
    jobs = result.get("job_ids", [])
    if not isinstance(jobs, list) or not all(
        isinstance(item, str) and item for item in jobs
    ):
        raise LifecycleError(f"coding lifecycle stage {stage} job IDs are invalid")
    return result


def _save_effect(record: dict[str, Any], stage: str, result: Mapping[str, Any]) -> None:
    prior = record["effects"].get(stage)
    evidence = {
        "receipt": result.get("receipt"),
        "receipt_hash": result.get("receipt_hash"),
        "idempotency_key": result["idempotency_key"],
    }
    if prior is not None and prior != evidence:
        raise LifecycleError("duplicate lifecycle effect has conflicting receipt")
    record["effects"][stage] = evidence
    for job_id in result.get("job_ids", []):
        if job_id not in record["job_ids"]:
            record["job_ids"].append(job_id)


def _next_stage(record: dict[str, Any], stage: str) -> None:
    index = STAGES.index(stage) + 1
    if index < len(STAGES):
        record["stage"] = STAGES[index]
        record["state"] = "RUNNING"
        return
    # Technical completion is truthful even when Context is unavailable and
    # no authenticated operator-acceptance source exists.  Acceptance remains
    # an independent PENDING projection; ordinary coding identities cannot
    # manufacture it.
    record["state"] = "TECHNICALLY_COMPLETE"


def _publication_due(record: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    value = record.get("publication", {}).get("next_retry_at")
    if value is None:
        return True
    try:
        due = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise LifecycleError("terminal publication retry time is invalid") from exc
    observed = now or datetime.now(timezone.utc)
    if due.tzinfo is None or due.utcoffset() is None:
        raise LifecycleError("terminal publication retry time is not timezone-aware")
    return observed >= due


def _publication_unavailable(
    record: dict[str, Any], result: Mapping[str, Any]
) -> None:
    attempts = int(record["publication"].get("attempts", 0)) + 1
    delay = min(900, 30 * (2 ** min(attempts - 1, 5)))
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    record["publication"].update(
        {
            "attempted": True,
            "attempts": attempts,
            "pending": True,
            "published": False,
            "last_error": result.get("reason", "Context unavailable"),
            "retry_available": True,
            "next_retry_at": retry_at.isoformat(),
        }
    )
    record["stages"]["terminal_publication"] = {
        **dict(result),
        "outcome": "deferred",
        "attempts": attempts,
    }


def _retry_publication(
    record: dict[str, Any], handlers: Mapping[str, StageHandler]
) -> None:
    if record["publication"].get("pending") is not True:
        return
    if not _publication_due(record):
        return
    handler = handlers.get("terminal_publication")
    if handler is None:
        return
    result = validate_stage_result(
        record, "terminal_publication", handler(dict(record))
    )
    if result["outcome"] == "publication_unavailable":
        _publication_unavailable(record, result)
        return
    if result["outcome"] != "satisfied":
        raise LifecycleError("terminal publication retry returned an unsafe outcome")
    _save_effect(record, "terminal_publication", result)
    record["stages"]["terminal_publication"] = result
    record["publication"].update(
        {
            "attempted": True,
            "attempts": max(1, int(record["publication"].get("attempts", 0))),
            "pending": False,
            "published": True,
            "retry_available": False,
            "receipt_hash": result["receipt_hash"],
            "next_retry_at": None,
        }
    )


def _begin_bounded_remediation(
    record: dict[str, Any], *, stage: str, result: Mapping[str, Any]
) -> bool:
    """Turn exact controller/review findings into one new fenced generation."""
    if stage not in {"controller", "review"}:
        return False
    receipt = result.get("receipt")
    candidate = receipt.get("candidate") if isinstance(receipt, Mapping) else None
    if (
        not isinstance(candidate, Mapping)
        or _COMMIT.fullmatch(str(candidate.get("head", ""))) is None
        or _COMMIT.fullmatch(str(candidate.get("tree", ""))) is None
        or not isinstance(result.get("receipt_hash"), str)
    ):
        return False
    history = record.setdefault("remediation_history", [])
    if not isinstance(history, list) or len(history) >= 3:
        return False
    archived = {
        "generation": len(history) + 1,
        "stage": stage,
        "stages": record.get("stages", {}),
        "effects": record.get("effects", {}),
        "job_ids": record.get("job_ids", []),
        "resume_intent": record.get("resume_intent"),
        "active_implementation_generation": record.get(
            "active_implementation_generation"
        ),
        "failure_result": dict(result),
    }
    archived["history_hash"] = _hash(archived)
    history.append(archived)
    intent = {
        "schema": "tgw-local-coding-remediation-intent/v1",
        "root_id": record["root_id"],
        "binding_hash": record["binding"]["binding_hash"],
        "generation": len(history),
        "failed_stage": stage,
        "failure_receipt_hash": result["receipt_hash"],
        "reason": result.get("reason", "diagnostic findings require remediation"),
        "candidate_commit": candidate["head"],
        "candidate_tree": candidate["tree"],
        "requested_at": _now(),
    }
    findings = receipt.get("findings") if isinstance(receipt, Mapping) else None
    if isinstance(findings, list) and findings and all(
        isinstance(item, Mapping) for item in findings
    ):
        intent["diagnostic_findings"] = [dict(item) for item in findings]
    record["remediation_intent"] = {
        **intent,
        "remediation_intent_hash": _hash(intent),
    }
    # The resume generation is preserved in the history above.  It must not
    # remain live beside remediation because implementation dispatch treats a
    # live resume intent as resume-only work.
    record.pop("resume_intent", None)
    record.pop("active_implementation_generation", None)
    record["stages"] = {}
    record["effects"] = {}
    record["job_ids"] = []
    record["stage"] = "implementation"
    record["state"] = "WAITING"
    record.pop("failure", None)
    return True


def advance(
    store: LifecycleStore,
    identity: str,
    handlers: Mapping[str, StageHandler],
) -> dict[str, Any]:
    """Advance until waiting/terminal; replay is safe after every restart."""

    lock = store.locked(identity)
    try:
        record = store.get(identity)
        if record is None:
            raise LifecycleError("coding lifecycle does not exist")
        original = _canonical(record)
        if record["state"] in TERMINAL:
            if record.get("publication", {}).get("pending") and _publication_due(
                record
            ):
                try:
                    _retry_publication(record, handlers)
                except LifecycleError as exc:
                    _publication_unavailable(
                        record,
                        {
                            "outcome": "publication_unavailable",
                            "idempotency_key": stage_idempotency_key(
                                record, "terminal_publication"
                            ),
                            "reason": str(exc),
                        },
                    )
            if _canonical(record) == original:
                return record
            record["updated_at"] = _now()
            return store.put(record)
        if record["state"] == "RESUMABLE_PARTIAL":
            return record
        publication_retried = False
        if record["publication"].get("pending") and _publication_due(record):
            try:
                _retry_publication(record, handlers)
            except LifecycleError as exc:
                _publication_unavailable(
                    record,
                    {
                        "outcome": "publication_unavailable",
                        "idempotency_key": stage_idempotency_key(
                            record, "terminal_publication"
                        ),
                        "reason": str(exc),
                    },
                )
            publication_retried = True
        while record["state"] not in TERMINAL:
            stage = record["stage"]
            if stage not in STAGES:
                raise LifecycleError("coding lifecycle stage is invalid")
            prior = record["stages"].get(stage)
            if isinstance(prior, dict) and prior.get("outcome") == "satisfied":
                _next_stage(record, stage)
                if record["state"] in TERMINAL:
                    break
                continue
            if stage == "terminal_publication" and isinstance(prior, dict) and prior.get(
                "outcome"
            ) == "deferred":
                _next_stage(record, stage)
                continue
            handler = handlers.get(stage)
            if handler is None:
                record["state"] = "REMEDIATION_REQUIRED"
                record["failure"] = {
                    "stage": stage,
                    "reason": "typed stage handler unavailable",
                }
                break
            if not isinstance(prior, dict) or prior.get("status") != "executing":
                record["stages"][stage] = {
                    "status": "executing",
                    "outcome": "waiting",
                    "idempotency_key": stage_idempotency_key(record, stage),
                    "started_at": _now(),
                }
                record["state"] = "RUNNING"
                record = store.put(record)
            try:
                result = validate_stage_result(
                    record, stage, handler(dict(record))
                )
            except (OSError, RuntimeError, ValueError) as exc:
                record["state"] = "REMEDIATION_REQUIRED"
                record["failure"] = {"stage": stage, "reason": str(exc)}
                break
            outcome = result["outcome"]
            if stage == "implementation":
                # A fast worker may become terminal between dispatch and the
                # first queue read. Bind/consume the one-shot intent whenever
                # the exact job is visible, not only on an observed WAITING.
                _bind_active_implementation_generation(record, result)
            if outcome == "waiting":
                record["state"] = "WAITING"
                record["stages"][stage] = result
                break
            if outcome == "resumable_partial":
                record["state"] = "RESUMABLE_PARTIAL"
                record["stages"][stage] = result
                record["failure"] = {
                    "stage": stage,
                    "reason": result.get("reason", "exact resume required"),
                }
                break
            if outcome == "publication_unavailable":
                if stage != "terminal_publication":
                    record["state"] = "REMEDIATION_REQUIRED"
                    record["failure"] = {
                        "stage": stage,
                        "reason": "publication-unavailable outside Context publication",
                    }
                    break
                _publication_unavailable(record, result)
                _next_stage(record, stage)
                # Do not republish merely because later internal stages changed.
                publication_retried = True
                continue
            if outcome != "satisfied":
                if _begin_bounded_remediation(
                    record, stage=stage, result=result
                ):
                    record["updated_at"] = _now()
                    record = store.put(record)
                    continue
                record["state"] = (
                    "FAILED" if outcome == "failed" else "REMEDIATION_REQUIRED"
                )
                record["stages"][stage] = result
                record["failure"] = {
                    "stage": stage,
                    "reason": result.get("reason", outcome),
                }
                break
            _save_effect(record, stage, result)
            record["stages"][stage] = result
            if stage == "terminal_publication":
                record["publication"].update(
                    {
                        "attempted": True,
                        "attempts": max(
                            1, int(record["publication"].get("attempts", 0))
                        ),
                        "pending": False,
                        "published": True,
                        "retry_available": False,
                        "receipt_hash": result["receipt_hash"],
                        "next_retry_at": None,
                    }
                )
            if stage == "operator_notification":
                record["operator"]["notification"] = {
                    "receipt": result["receipt"],
                    "receipt_hash": result["receipt_hash"],
                }
            _next_stage(record, stage)
            if record["state"] in TERMINAL:
                break
        if publication_retried and record["publication"].get("pending"):
            record["publication"]["retry_available"] = True
        candidate = {key: value for key, value in record.items() if key != "record_hash"}
        before = json.loads(original)
        before.pop("record_hash", None)
        if candidate == before:
            return record
        record["updated_at"] = _now()
        return store.put(record)
    finally:
        lock.close()


def request_resume(
    store: LifecycleStore, identity: str, *, receipt: Mapping[str, Any]
) -> dict[str, Any]:
    """Journal the exact resume intent before any queue dispatch can occur."""

    lock = store.locked(identity)
    try:
        record = store.get(identity)
        if record is None:
            raise LifecycleError("coding lifecycle does not exist")
        if (
            record.get("resume_intent") is not None
            and record["state"] != "RESUMABLE_PARTIAL"
        ):
            return record
        if record["state"] != "RESUMABLE_PARTIAL":
            raise LifecycleError("coding lifecycle is not RESUMABLE_PARTIAL")
        value = dict(receipt)
        required = {
            "schema",
            "root_id",
            "binding_hash",
            "todo_id",
            "resume_of",
            "resume_fingerprint",
            "worktree",
            "source_commit",
            "source_tree",
        }
        if (
            set(value) != required
            or value.get("schema") != "tgw-local-coding-lifecycle-resume-intent/v1"
            or value.get("root_id") != identity
            or value.get("binding_hash") != record["binding"]["binding_hash"]
            or value.get("todo_id") != int(record["target"])
            or value.get("worktree") != record["binding"]["worktree"]
            or value.get("source_commit") != record["binding"]["source_commit"]
            or value.get("source_tree") != record["binding"]["source_tree"]
            or _SHA256.fullmatch(str(value.get("resume_of", ""))) is None
            or _SHA256.fullmatch(str(value.get("resume_fingerprint", ""))) is None
        ):
            raise LifecycleError("coding lifecycle resume receipt binding mismatch")
        archived = {
            "stages": record.get("stages", {}),
            "effects": record.get("effects", {}),
            "job_ids": record.get("job_ids", []),
            "resume_intent": record.get("resume_intent"),
            "active_implementation_generation": record.get(
                "active_implementation_generation"
            ),
        }
        record.setdefault("resume_history", []).append(
            {**archived, "history_hash": _hash(archived)}
        )
        generation = int(record.get("resume_generation", 0)) + 1
        intent = {
            **value,
            "generation": generation,
            "requested_at": _now(),
        }
        record["resume_generation"] = generation
        record["resume_intent"] = {
            **intent,
            "resume_intent_hash": _hash(intent),
        }
        record.pop("active_implementation_generation", None)
        record["stages"] = {}
        record["effects"] = {}
        record["job_ids"] = []
        record["stage"] = "implementation"
        record["state"] = "WAITING"
        record.pop("failure", None)
        record["updated_at"] = _now()
        return store.put(record)
    finally:
        lock.close()


def record_operator_readback(
    store: LifecycleStore,
    identity: str,
    *,
    actor: str,
) -> dict[str, Any]:
    """Record readback only; this ordinary surface never records acceptance."""
    if not isinstance(actor, str) or not actor:
        raise LifecycleError("operator readback actor is required")
    lock = store.locked(identity)
    try:
        record = store.get(identity)
        if record is None:
            raise LifecycleError("coding lifecycle does not exist")
        notification = record.get("operator", {}).get("notification")
        if not isinstance(notification, Mapping):
            raise LifecycleError("operator notification has not been published")
        existing = record["operator"].get("readback")
        if isinstance(existing, Mapping):
            return record
        unsigned = {
            "schema": "tgw-local-coding-operator-readback/v1",
            "root_id": identity,
            "binding_hash": record["binding"]["binding_hash"],
            "notification_receipt_hash": notification["receipt_hash"],
            "actor": actor,
            "observed_at": _now(),
        }
        readback = {**unsigned, "readback_hash": _hash(unsigned)}
        record["operator"]["readback"] = readback
        record["operator"]["acceptance"] = "PENDING"
        record["operator_acceptance"] = "PENDING"
        record["updated_at"] = _now()
        return store.put(record)
    finally:
        lock.close()


def run_supervisor(
    identity: str,
    *,
    config_path: Path | str,
    poll_interval: float = 2.0,
) -> dict[str, Any] | None:
    """Own one root until terminal, including partial and Context recovery."""

    from tgw.coding_cli import supervise
    from tgw.development.local_workflow import load_config

    config = load_config(config_path)
    store = LifecycleStore(config["coding"]["lifecycle_root"])
    owner = store.supervisor_lock(identity)
    if owner is None:
        return None
    try:
        while True:
            record = supervise(identity, config_path=Path(config_path))
            if record["state"] in TERMINAL:
                return record
            time.sleep(max(0.05, poll_interval))
    finally:
        owner.close()


def run_managed_supervisor(
    *,
    config_path: Path | str,
    poll_interval: float = 2.0,
    once: bool = False,
) -> list[dict[str, Any]]:
    """Continuously reconstruct every lifecycle from journals after restart.

    One systemd-managed process owns this loop.  It creates no child process,
    does not rely on the Foreman timer's cgroup, and treats an unchanged poll as
    observational so journals are not replaced/fsynced on a cadence.
    """

    from tgw.coding_cli import supervise
    from tgw.development.local_workflow import load_config

    config = load_config(config_path)
    lifecycle_root = Path(config["coding"]["lifecycle_root"])
    canary_gid = os.environ.get("TGW_CODING_DISPOSABLE_CANARY_GID")
    group_gid = None
    if canary_gid is not None:
        temporary_root = Path(tempfile.gettempdir()).resolve()
        try:
            lifecycle_root.resolve().relative_to(temporary_root)
        except ValueError as exc:
            raise LifecycleError(
                "disposable canary group override is outside the temp root"
            ) from exc
        if not once or not canary_gid.isdigit():
            raise LifecycleError("disposable canary group override is invalid")
        group_gid = int(canary_gid)
    store = (
        LifecycleStore(lifecycle_root)
        if group_gid is None
        else LifecycleStore(lifecycle_root, group_gid=group_gid)
    )
    observed: list[dict[str, Any]] = []
    while True:
        observed = []
        for record in store.records():
            pending_projection = record.get("publication", {}).get("pending") is True
            if record["state"] in TERMINAL and not (
                pending_projection and _publication_due(record)
            ):
                continue
            result = supervise(record["root_id"], config_path=Path(config_path))
            observed.append(
                {"root_id": result["root_id"], "state": result["state"]}
            )
        if once:
            return observed
        time.sleep(max(0.05, poll_interval))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    selected = parser.add_mutually_exclusive_group(required=True)
    selected.add_argument("--resume")
    selected.add_argument("--managed", action="store_true")
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.managed:
        run_managed_supervisor(config_path=Path(args.config), once=args.once)
    else:
        run_supervisor(args.resume, config_path=Path(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
