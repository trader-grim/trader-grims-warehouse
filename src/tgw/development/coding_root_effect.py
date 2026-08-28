"""Fixed-schema root consumer for local coding materialization and verification.

Ordinary ``tgw-coders`` publish one self-hashed request derived from the
read-only lifecycle journal.  This root service accepts no command, provider,
approval, admission, or remote-effect fields.  Every step is reconstructed
from the candidate and the source-defined Doctor/release primitives, and each
step is replay-safe after a process or host restart.
"""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.candidate_receipt_sink import (
    CandidateReceiptSinkError,
    PinnedCandidateEvidenceDescriptor,
    PinnedGitReceiptSink,
    validate_receipt_sink_descriptor,
    verify_governed_execution_bundle,
)
from tgw.development.coding_lifecycle import LifecycleError, LifecycleStore, job_binding
from tgw.development.coding_review import (
    DEFAULT_PROTECTED_REVIEW_CONFIG,
    load_protected_review_config,
    validate_review_artifact,
)
from tgw.development.coding_review_protection import prepare_governed_request
from tgw.release_installer import (
    _select as select_fixed_local,  # the fixed CAS primitive; never admission
)
from tgw.release_installer import current_generation, materialize, verify
from tgw.review_contract import ReviewRunnerError

REQUEST_SCHEMA = "tgw-local-coding-root-effect-request/v1"
RESPONSE_SCHEMA = "tgw-local-coding-root-effect-response/v1"
STATE_SCHEMA = "tgw-local-coding-root-effect-state/v1"
PROJECTION_SCHEMA = "tgw-local-coding-context-projection-request/v1"
PROJECTION_RESPONSE_SCHEMA = "tgw-local-coding-context-projection-response/v1"
PREPARATION_SCHEMA = "tgw-local-coding-review-preparation-request/v1"
PREPARATION_RESPONSE_SCHEMA = "tgw-local-coding-review-preparation-response/v1"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ROOT = re.compile(r"coding:[0-9a-f]{64}\Z")
_FORBIDDEN = frozenset(
    {
        "argv",
        "command",
        "shell",
        "provider",
        "approval",
        "admission",
        "ssh",
        "remote",
        "actor_fleet",
        "memory",
        "production",
    }
)


class RootEffectError(RuntimeError):
    """A root request or its persisted effect state is unsafe."""


class ProtectedReviewEvidenceError(RootEffectError):
    """Protected governed-review evidence is absent or does not bind."""


@dataclass(frozen=True)
class RootEffectPaths:
    request_root: Path
    lifecycle_root: Path
    repository: Path
    runtime_root: Path
    coding_config: Path
    protected_review_config: Path = DEFAULT_PROTECTED_REVIEW_CONFIG
    context_task: Path = Path(
        "/opt/TGW/tgw-lib/context-input/current-task.json"
    )
    group_gid: int | None = None
    root_uid: int = 0

    @classmethod
    def from_config(cls, path: Path | str) -> "RootEffectPaths":
        from tgw.development.local_workflow import load_config

        config = load_config(path)
        coding = config["coding"]
        return cls(
            request_root=Path(coding["root_effect_root"]),
            lifecycle_root=Path(coding["lifecycle_root"]),
            repository=Path(coding["repository_root"]),
            runtime_root=Path(coding["runtime_root"]),
            coding_config=Path(path),
            # Candidate source cannot redirect the root-side review trust root.
            protected_review_config=DEFAULT_PROTECTED_REVIEW_CONFIG,
        )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _assert_no_forbidden(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(token in normalized for token in _FORBIDDEN):
                raise RootEffectError(f"root effect request contains forbidden field: {key}")
            _assert_no_forbidden(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden(item)


def _stage_hash(record: Mapping[str, Any], stage: str) -> str:
    value = record.get("effects", {}).get(stage, {}).get("receipt_hash")
    if _SHA256.fullmatch(str(value or "")) is None:
        raise RootEffectError(f"lifecycle {stage} receipt is absent")
    return str(value)


def build_request(record: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the sole privileged request from already completed stages."""

    binding = record.get("binding", {})
    candidate = record.get("effects", {}).get("candidate", {}).get("receipt", {})
    review = record.get("effects", {}).get("review", {}).get("receipt", {})
    review_result = review.get("result") if isinstance(review, Mapping) else None
    if not isinstance(review_result, Mapping):
        raise RootEffectError("lifecycle independent-review result is absent")
    try:
        validate_review_artifact(
            review_result,
            payload=review_result,
            worktree=Path(binding["worktree"]),
            expected_job_id=str(review["job_id"]),
        )
    except (KeyError, ReviewRunnerError) as exc:
        raise RootEffectError(f"lifecycle independent-review result is invalid: {exc}") from exc
    unsigned = {
        "schema": REQUEST_SCHEMA,
        "root_id": record.get("root_id"),
        "binding_hash": binding.get("binding_hash"),
        "plan_commit": binding.get("plan_commit"),
        "solution_hash": binding.get("solution_hash"),
        "closure_hash": binding.get("closure_hash"),
        "source_commit": binding.get("source_commit"),
        "source_tree": binding.get("source_tree"),
        "execution_root_identity": binding.get("execution_root_identity"),
        "card_idempotency_key": binding.get("card_idempotency_key"),
        "candidate_commit": candidate.get("commit"),
        "candidate_tree": candidate.get("tree"),
        "candidate_receipt_hash": _stage_hash(record, "candidate"),
        "controller_receipt_hash": _stage_hash(record, "controller"),
        "review_receipt_hash": _stage_hash(record, "review"),
        "integration_receipt_hash": _stage_hash(record, "integration"),
    }
    _assert_no_forbidden(unsigned)
    return {**unsigned, "request_hash": _hash(unsigned)}


def validate_request(
    value: object,
    *,
    store: LifecycleStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a canonical request against its read-only lifecycle journal."""

    if not isinstance(value, Mapping):
        raise RootEffectError("root effect request is not an object")
    request = dict(value)
    _assert_no_forbidden(request)
    expected_fields = {
        "schema",
        "root_id",
        "binding_hash",
        "plan_commit",
        "solution_hash",
        "closure_hash",
        "source_commit",
        "source_tree",
        "execution_root_identity",
        "card_idempotency_key",
        "candidate_commit",
        "candidate_tree",
        "candidate_receipt_hash",
        "controller_receipt_hash",
        "review_receipt_hash",
        "integration_receipt_hash",
        "request_hash",
    }
    unsigned = {key: item for key, item in request.items() if key != "request_hash"}
    if (
        set(request) != expected_fields
        or request.get("schema") != REQUEST_SCHEMA
        or _ROOT.fullmatch(str(request.get("root_id", ""))) is None
        or request.get("request_hash") != _hash(unsigned)
        or _SHA256.fullmatch(str(request.get("binding_hash", ""))) is None
        or _COMMIT.fullmatch(str(request.get("plan_commit", ""))) is None
        or _COMMIT.fullmatch(str(request.get("source_commit", ""))) is None
        or _COMMIT.fullmatch(str(request.get("source_tree", ""))) is None
        or _COMMIT.fullmatch(str(request.get("candidate_commit", ""))) is None
        or _COMMIT.fullmatch(str(request.get("candidate_tree", ""))) is None
        or any(
            _SHA256.fullmatch(str(request.get(field, ""))) is None
            for field in (
                "solution_hash",
                "closure_hash",
                "execution_root_identity",
                "card_idempotency_key",
                "candidate_receipt_hash",
                "controller_receipt_hash",
                "review_receipt_hash",
                "integration_receipt_hash",
            )
        )
    ):
        raise RootEffectError("root effect request schema/hash is invalid")
    record = store.get(str(request["root_id"]))
    if record is None or build_request(record) != request:
        raise RootEffectError("root effect request differs from lifecycle journal")
    return request, record


def _group_gid(paths: RootEffectPaths) -> int:
    if paths.group_gid is not None:
        return paths.group_gid
    try:
        return grp.getgrnam("tgw-coders").gr_gid
    except KeyError as exc:
        raise RootEffectError("tgw-coders group is unavailable") from exc


def verify_protected_review_evidence(
    paths: RootEffectPaths,
    request: Mapping[str, Any],
    record: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify exact independent-review evidence outside coding-group state.

    Lifecycle documents and root-effect requests are ordinary-user triggers.
    This protected, independently pinned governed receipt is the only review
    evidence the privileged consumer accepts before candidate bytes are used.
    """

    from tgw.context_generation_status import (
        ContextGenerationStatusError,
        _protected_directory,
        _protected_json,
    )

    try:
        configuration = load_protected_review_config(
            paths.protected_review_config,
            candidate_repository=paths.repository,
            trusted_uid=paths.root_uid,
        )
        published = configuration["execution_evidence_pin_source"]
        _protected_directory(
            published.parent,
            "coding protected-review execution evidence publication parent",
            paths.root_uid,
        )
        published_value = _protected_json(
            published,
            "coding protected-review execution evidence publication",
            paths.root_uid,
        )
        validate_receipt_sink_descriptor(published_value)
        execution_config = configuration["execution_evidence_sink_config"]
        try:
            installed_execution = _protected_json(
                execution_config,
                "coding protected-review execution evidence binding",
                paths.root_uid,
            )
        except ContextGenerationStatusError:
            installed_execution = None
        if installed_execution != published_value:
            if os.geteuid() != paths.root_uid:
                raise ProtectedReviewEvidenceError(
                    "protected execution evidence binding requires root refresh"
                )
            from tgw.development.coding_review_protection import _atomic_root_json

            _atomic_root_json(execution_config, published_value, mode=0o400)
        protected_values: dict[str, dict[str, Any]] = {}
        for name in (
            "candidate_evidence_descriptor_config",
            "execution_evidence_sink_config",
        ):
            configured = configuration[name]
            _protected_directory(
                configured.parent,
                f"coding protected-review {name} parent",
                paths.root_uid,
            )
            protected_values[name] = _protected_json(
                configured,
                f"coding protected-review {name}",
                paths.root_uid,
            )
        descriptor = PinnedCandidateEvidenceDescriptor(
            protected_values["candidate_evidence_descriptor_config"],
            candidate_repository=paths.repository,
        )
        execution_sink = PinnedGitReceiptSink(
            protected_values["execution_evidence_sink_config"],
            candidate_repository=paths.repository,
        )
        verified = verify_governed_execution_bundle(
            execution_sink,
            candidate_evidence_descriptor=descriptor,
            source_commit=str(request["candidate_commit"]),
            source_tree=str(request["candidate_tree"]),
            plan_commit=str(request["plan_commit"]),
            role="independent-review",
        )
    except (
        CandidateReceiptSinkError,
        ContextGenerationStatusError,
        OSError,
        ReviewRunnerError,
        ValueError,
    ) as exc:
        raise ProtectedReviewEvidenceError(
            "exact protected governed independent-review evidence is unavailable"
        ) from exc

    role_receipt = verified["role_receipt"]
    governed_receipt = verified["receipt"]
    card = verified["card"]
    card_bindings = card.get("bindings")
    if (
        not isinstance(card_bindings, Mapping)
        or card.get("role") != "independent-review"
        or card.get("plan_commit") != request.get("plan_commit")
        or card.get("solution_id") != request.get("solution_hash")
        or descriptor.w06_plan_materialization_pin.get("plan_source", {}).get(
            "commit"
        )
        != request.get("plan_commit")
    ):
        raise ProtectedReviewEvidenceError(
            "protected governed review Plan/card binding is incomplete"
        )
    execution_artifacts = [
        artifact
        for artifact in role_receipt.get("artifacts", [])
        if isinstance(artifact, Mapping)
        and artifact.get("kind") == "governed_review_execution"
        and set(artifact) == {"kind", "execution_hash"}
    ]
    if (
        len(execution_artifacts) != 1
        or _SHA256.fullmatch(
            str(execution_artifacts[0].get("execution_hash", ""))
        )
        is None
    ):
        raise ProtectedReviewEvidenceError(
            "protected governed review execution identity is incomplete"
        )

    review_receipt = (
        record.get("effects", {}).get("review", {}).get("receipt", {})
    )
    review_result = review_receipt.get("result")
    if not isinstance(review_result, Mapping):
        raise ProtectedReviewEvidenceError(
            "lifecycle governed-review projection is absent"
        )
    try:
        projected = validate_review_artifact(
            review_result,
            payload=review_result,
            worktree=Path(str(record.get("binding", {}).get("worktree", ""))),
            expected_job_id=str(review_receipt.get("job_id", "")),
        )["protected_review"]
    except (OSError, ReviewRunnerError, ValueError) as exc:
        raise ProtectedReviewEvidenceError(
            "lifecycle governed-review projection is invalid"
        ) from exc
    if (
        projected.get("execution_hash")
        != execution_artifacts[0]["execution_hash"]
        or projected.get("role_receipt_hash")
        != role_receipt.get("receipt_hash")
        or projected.get("governed_bundle_hash") != verified.get("bundle_hash")
    ):
        raise ProtectedReviewEvidenceError(
            "lifecycle review projection differs from protected governed evidence"
        )
    unsigned = {
        "schema": "tgw-local-coding-protected-review-evidence/v1",
        "role": "independent-review",
        "candidate_commit": request["candidate_commit"],
        "candidate_tree": request["candidate_tree"],
        "plan_commit": request["plan_commit"],
        "governed_bundle_hash": verified["bundle_hash"],
        "candidate_receipt_hash": governed_receipt["receipt_hash"],
        "role_receipt_hash": role_receipt["receipt_hash"],
        "execution_hash": execution_artifacts[0]["execution_hash"],
    }
    return {**unsigned, "protected_review_hash": _hash(unsigned)}


def _safe_root(root: Path, *, group_gid: int, root_uid: int = 0) -> None:
    if not root.exists():
        raise RootEffectError("root effect directory has not been provisioned by Doctor")
    state = root.stat(follow_symlinks=False)
    if (
        root.is_symlink()
        or not stat.S_ISDIR(state.st_mode)
        or state.st_uid != root_uid
        or state.st_gid != group_gid
        or stat.S_IMODE(state.st_mode) != 0o3770
    ):
        raise RootEffectError("root effect directory is not protected")


def _atomic(
    path: Path,
    value: Mapping[str, Any],
    *,
    replace: bool,
    group_gid: int,
    mode: int = 0o660,
    owner_uid: int | None = None,
    directory_uid: int = 0,
) -> None:
    _safe_root(path.parent, group_gid=group_gid, root_uid=directory_uid)
    raw = _canonical(value) + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".root-effect-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchown(descriptor, -1 if owner_uid is None else owner_uid, group_gid)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if not replace and path.exists():
            if _load_exact(
                path,
                expected_uid=owner_uid,
                expected_gid=group_gid,
                expected_mode=mode,
            ) != dict(value):
                raise RootEffectError("immutable root effect artifact conflicts")
            return
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def request_path(paths: RootEffectPaths, root_id: str) -> Path:
    if _ROOT.fullmatch(root_id) is None:
        raise RootEffectError("root effect identity is invalid")
    return paths.request_root / f"{root_id.removeprefix('coding:')}.request.json"


def response_path(paths: RootEffectPaths, root_id: str) -> Path:
    if _ROOT.fullmatch(root_id) is None:
        raise RootEffectError("root effect identity is invalid")
    return paths.request_root / f"{root_id.removeprefix('coding:')}.response.json"


def preparation_path(paths: RootEffectPaths, root_id: str) -> Path:
    if _ROOT.fullmatch(root_id) is None:
        raise RootEffectError("review preparation identity is invalid")
    return paths.request_root / (
        f"{root_id.removeprefix('coding:')}.review-preparation-request.json"
    )


def preparation_response_path(paths: RootEffectPaths, root_id: str) -> Path:
    if _ROOT.fullmatch(root_id) is None:
        raise RootEffectError("review preparation identity is invalid")
    return paths.request_root / (
        f"{root_id.removeprefix('coding:')}.review-preparation-response.json"
    )


def build_review_preparation_request(
    record: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an ordinary trigger containing no protected path or result field."""

    binding = record.get("binding", {})
    candidate = record.get("effects", {}).get("candidate", {}).get("receipt", {})
    lifecycle = job_binding(record)
    unsigned = {
        "schema": PREPARATION_SCHEMA,
        "root_id": record.get("root_id"),
        "binding_hash": binding.get("binding_hash"),
        "job_binding_hash": lifecycle.get("job_binding_hash"),
        "card_idempotency_key": binding.get("card_idempotency_key"),
        "plan_commit": binding.get("plan_commit"),
        "solution_hash": binding.get("solution_hash"),
        "closure_hash": binding.get("closure_hash"),
        "candidate_commit": candidate.get("commit"),
        "candidate_tree": candidate.get("tree"),
        "candidate_receipt_hash": _stage_hash(record, "candidate"),
    }
    _assert_no_forbidden(unsigned)
    return {**unsigned, "preparation_hash": _hash(unsigned)}


def validate_review_preparation_request(
    value: object, *, store: LifecycleStore
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise RootEffectError("review preparation request is not an object")
    request = dict(value)
    _assert_no_forbidden(request)
    required = {
        "schema",
        "root_id",
        "binding_hash",
        "job_binding_hash",
        "card_idempotency_key",
        "plan_commit",
        "solution_hash",
        "closure_hash",
        "candidate_commit",
        "candidate_tree",
        "candidate_receipt_hash",
        "preparation_hash",
    }
    unsigned = {
        key: item for key, item in request.items() if key != "preparation_hash"
    }
    if (
        set(request) != required
        or request.get("schema") != PREPARATION_SCHEMA
        or request.get("preparation_hash") != _hash(unsigned)
        or _ROOT.fullmatch(str(request.get("root_id", ""))) is None
        or any(
            _COMMIT.fullmatch(str(request.get(field, ""))) is None
            for field in ("plan_commit", "candidate_commit", "candidate_tree")
        )
        or any(
            _SHA256.fullmatch(str(request.get(field, ""))) is None
            for field in (
                "binding_hash",
                "job_binding_hash",
                "card_idempotency_key",
                "solution_hash",
                "closure_hash",
                "candidate_receipt_hash",
            )
        )
    ):
        raise RootEffectError("review preparation request schema/hash is invalid")
    record = store.get(str(request["root_id"]))
    if record is None or build_review_preparation_request(record) != request:
        raise RootEffectError("review preparation differs from lifecycle candidate")
    return request, record


def ensure_review_preparation_request(
    paths: RootEffectPaths, record: Mapping[str, Any]
) -> dict[str, Any]:
    request = build_review_preparation_request(record)
    _atomic(
        preparation_path(paths, str(request["root_id"])),
        request,
        replace=False,
        group_gid=_group_gid(paths),
        directory_uid=paths.root_uid,
    )
    return request


def read_review_preparation_response(
    paths: RootEffectPaths, request: Mapping[str, Any]
) -> dict[str, Any] | None:
    value = _root_document_or_absent(
        paths, preparation_response_path(paths, str(request["root_id"]))
    )
    if value is None:
        return None
    unsigned = {key: item for key, item in value.items() if key != "response_hash"}
    try:
        expires = datetime.fromisoformat(
            str(value.get("expires_at", "")).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise RootEffectError("review preparation response time is invalid") from exc
    if (
        value.get("schema") != PREPARATION_RESPONSE_SCHEMA
        or value.get("status") != "PREPARED"
        or value.get("root_id") != request.get("root_id")
        or value.get("binding_hash") != request.get("binding_hash")
        or value.get("preparation_hash") != request.get("preparation_hash")
        or value.get("candidate_commit") != request.get("candidate_commit")
        or value.get("candidate_tree") != request.get("candidate_tree")
        or value.get("plan_commit") != request.get("plan_commit")
        or value.get("response_hash") != _hash(unsigned)
        or any(
            _SHA256.fullmatch(str(value.get(field, ""))) is None
            for field in ("request_sha256", "snapshot_hash", "card_hash", "packet_hash")
        )
        or expires.tzinfo is None
        or expires.utcoffset() is None
    ):
        raise RootEffectError("review preparation response binding is invalid")
    if datetime.now(timezone.utc) + timedelta(seconds=30) >= expires:
        return None
    return value


def process_review_preparation(
    paths: RootEffectPaths,
    value: object,
    *,
    store: LifecycleStore | None = None,
    preparer: Callable[..., Mapping[str, Any]] = prepare_governed_request,
) -> dict[str, Any]:
    journal = store or LifecycleStore(
        paths.lifecycle_root, group_gid=_group_gid(paths)
    )
    request, _record = validate_review_preparation_request(value, store=journal)
    prior = read_review_preparation_response(paths, request)
    if prior is not None:
        return prior
    configuration = load_protected_review_config(
        paths.protected_review_config,
        candidate_repository=paths.repository,
        trusted_uid=paths.root_uid,
    )
    prepared = dict(
        preparer(
            repository=paths.repository,
            candidate_commit=str(request["candidate_commit"]),
            candidate_tree=str(request["candidate_tree"]),
            plan_commit=str(request["plan_commit"]),
            solution_hash=str(request["solution_hash"]),
            closure_hash=str(request["closure_hash"]),
            profile_path=configuration["request_profile_config"],
            candidate_descriptor_path=configuration[
                "candidate_evidence_descriptor_config"
            ],
            request_root=configuration["request_root"],
            snapshot_root=configuration["snapshot_root"],
            resource_registry_root=configuration["resource_registry_root"],
            broker_grant_root=configuration["broker_grant_root"],
            credential_paths={
                "context": configuration["context_credential_config"],
                "evidence": configuration["evidence_credential_config"],
                "resource": configuration["resource_credential_config"],
                "broker": configuration["broker_credential_config"],
            },
            trusted_uid=paths.root_uid,
        )
    )
    if set(prepared) != {
        "request_path",
        "request_sha256",
        "snapshot",
        "snapshot_hash",
        "expires_at",
        "card_hash",
        "packet_hash",
    }:
        raise RootEffectError("protected review preparer returned incomplete evidence")
    unsigned = {
        "schema": PREPARATION_RESPONSE_SCHEMA,
        "status": "PREPARED",
        "root_id": request["root_id"],
        "binding_hash": request["binding_hash"],
        "preparation_hash": request["preparation_hash"],
        "candidate_commit": request["candidate_commit"],
        "candidate_tree": request["candidate_tree"],
        "plan_commit": request["plan_commit"],
        **prepared,
    }
    response = {**unsigned, "response_hash": _hash(unsigned)}
    _atomic(
        preparation_response_path(paths, str(request["root_id"])),
        response,
        replace=True,
        group_gid=_group_gid(paths),
        mode=0o640,
        owner_uid=paths.root_uid,
        directory_uid=paths.root_uid,
    )
    return response


def projection_path(paths: RootEffectPaths, root_id: str) -> Path:
    if _ROOT.fullmatch(root_id) is None:
        raise RootEffectError("Context projection identity is invalid")
    return paths.request_root / (
        f"{root_id.removeprefix('coding:')}.projection-request.json"
    )


def projection_response_path(paths: RootEffectPaths, root_id: str) -> Path:
    if _ROOT.fullmatch(root_id) is None:
        raise RootEffectError("Context projection identity is invalid")
    return paths.request_root / (
        f"{root_id.removeprefix('coding:')}.projection-response.json"
    )


def ensure_request(paths: RootEffectPaths, record: Mapping[str, Any]) -> dict[str, Any]:
    request = build_request(record)
    _atomic(
        request_path(paths, str(request["root_id"])),
        request,
        replace=False,
        group_gid=_group_gid(paths),
        directory_uid=paths.root_uid,
    )
    return request


def build_projection_request(record: Mapping[str, Any]) -> dict[str, Any]:
    """Build the one terminal-only, non-authoritative Context projection."""

    prior_projection = record.get("stages", {}).get("terminal_publication", {})
    if record.get("stage") != "terminal_publication" and not (
        isinstance(prior_projection, Mapping)
        and prior_projection.get("outcome") == "deferred"
        and record.get("effects", {}).get("live_verification") is not None
    ):
        raise RootEffectError("Context projection is not at its terminal stage")
    binding = record.get("binding", {})
    candidate = record.get("effects", {}).get("candidate", {}).get("receipt", {})
    live = record.get("effects", {}).get("live_verification", {}).get("receipt", {})
    technical_hash = live.get("technical_result_hash")
    if _SHA256.fullmatch(str(technical_hash or "")) is None:
        raise RootEffectError("terminal projection lacks its technical result")
    result_unsigned = {
        "schema": "tgw-local-coding-terminal-result/v1",
        "root_id": record.get("root_id"),
        "binding_hash": binding.get("binding_hash"),
        "candidate_commit": candidate.get("commit"),
        "candidate_tree": candidate.get("tree"),
        "review_receipt_hash": _stage_hash(record, "review"),
        "integration_receipt_hash": _stage_hash(record, "integration"),
        "materialization_receipt_hash": _stage_hash(record, "materialization"),
        "live_verification_receipt_hash": _stage_hash(record, "live_verification"),
        "technical_result_hash": technical_hash,
        "operator_acceptance": "PENDING",
    }
    unsigned = {
        "schema": PROJECTION_SCHEMA,
        "root_id": record.get("root_id"),
        "binding_hash": binding.get("binding_hash"),
        "plan_commit": binding.get("plan_commit"),
        "solution_hash": binding.get("solution_hash"),
        "closure_hash": binding.get("closure_hash"),
        "source_commit": binding.get("source_commit"),
        "source_tree": binding.get("source_tree"),
        "card_idempotency_key": binding.get("card_idempotency_key"),
        "candidate_commit": candidate.get("commit"),
        "candidate_tree": candidate.get("tree"),
        "candidate_receipt_hash": _stage_hash(record, "candidate"),
        "review_receipt_hash": _stage_hash(record, "review"),
        "integration_receipt_hash": _stage_hash(record, "integration"),
        "materialization_receipt_hash": _stage_hash(record, "materialization"),
        "live_verification_receipt_hash": _stage_hash(record, "live_verification"),
        "technical_result_hash": technical_hash,
        "result_hash": _hash(result_unsigned),
    }
    return {**unsigned, "projection_hash": _hash(unsigned)}


def ensure_projection_request(
    paths: RootEffectPaths, record: Mapping[str, Any]
) -> dict[str, Any]:
    value = build_projection_request(record)
    _atomic(
        projection_path(paths, str(value["root_id"])),
        value,
        replace=False,
        group_gid=_group_gid(paths),
        directory_uid=paths.root_uid,
    )
    return value


def read_projection_response(
    paths: RootEffectPaths, request: Mapping[str, Any]
) -> dict[str, Any] | None:
    path = projection_response_path(paths, str(request["root_id"]))
    value = _root_document_or_absent(paths, path)
    if value is None:
        return None
    unsigned = {key: item for key, item in value.items() if key != "response_hash"}
    if (
        value.get("schema") != PROJECTION_RESPONSE_SCHEMA
        or value.get("status") != "PUBLISHED"
        or value.get("root_id") != request.get("root_id")
        or value.get("projection_hash") != request.get("projection_hash")
        or value.get("result_hash") != request.get("result_hash")
        or value.get("response_hash") != _hash(unsigned)
        or _SHA256.fullmatch(str(value.get("context_receipt_file_sha256", "")))
        is None
        or _SHA256.fullmatch(str(value.get("context_task_file_sha256", "")))
        is None
    ):
        raise RootEffectError("Context projection response binding is invalid")
    return value


def _load_exact(
    path: Path,
    *,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    expected_mode: int | None = None,
) -> dict[str, Any]:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        state = os.fstat(descriptor)
        if (
            not stat.S_ISREG(state.st_mode)
            or state.st_nlink != 1
            or (expected_uid is not None and state.st_uid != expected_uid)
            or (expected_gid is not None and state.st_gid != expected_gid)
            or (
                expected_mode is not None
                and stat.S_IMODE(state.st_mode) != expected_mode
            )
        ):
            raise RootEffectError(
                f"root effect artifact ownership/type/mode is unsafe: {path.name}"
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RootEffectError(f"root effect artifact is unreadable: {path.name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict) or raw != _canonical(value) + b"\n":
        raise RootEffectError(f"root effect artifact is not canonical: {path.name}")
    return value


def _load_root_exact(paths: RootEffectPaths, path: Path) -> dict[str, Any]:
    return _load_exact(
        path,
        expected_uid=paths.root_uid,
        expected_gid=_group_gid(paths),
        expected_mode=0o640,
    )


def _trusted_root_file(paths: RootEffectPaths, path: Path, *, mode: int) -> bool:
    try:
        state = path.lstat()
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(state.st_mode)
        and state.st_nlink == 1
        and state.st_uid == paths.root_uid
        and state.st_gid == _group_gid(paths)
        and stat.S_IMODE(state.st_mode) == mode
    )


def _root_document_or_absent(
    paths: RootEffectPaths, path: Path
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return _load_root_exact(paths, path)
    except RootEffectError:
        try:
            owner = path.lstat().st_uid
        except FileNotFoundError:
            return None
        if owner != paths.root_uid:
            # A coding-group member may squat on a predictable response name,
            # but only the fixed root consumer may mint trusted evidence.  The
            # root-owned sticky parent lets the consumer atomically replace it.
            return None
        raise


def read_response(
    paths: RootEffectPaths,
    request: Mapping[str, Any],
) -> dict[str, Any] | None:
    path = response_path(paths, str(request["root_id"]))
    value = _root_document_or_absent(paths, path)
    if value is None:
        return None
    unsigned = {key: item for key, item in value.items() if key != "response_hash"}
    if (
        value.get("schema") != RESPONSE_SCHEMA
        or value.get("root_id") != request.get("root_id")
        or value.get("binding_hash") != request.get("binding_hash")
        or value.get("request_hash") != request.get("request_hash")
        or value.get("response_hash") != _hash(unsigned)
        or value.get("status") != "PASS"
        or value.get("candidate_commit") != request.get("candidate_commit")
        or value.get("candidate_tree") != request.get("candidate_tree")
    ):
        raise RootEffectError("root effect response binding is invalid")
    for key in (
        "protected_review_receipt_hash",
        "governed_review_bundle_hash",
        "materialization_receipt_hash",
        "selection_receipt_hash",
        "workers_receipt_hash",
        "live_verification_receipt_hash",
        "technical_result_hash",
    ):
        if _SHA256.fullmatch(str(value.get(key, ""))) is None:
            raise RootEffectError("root effect response receipt hashes are incomplete")
    return value


def _git(paths: RootEffectPaths, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={paths.repository.resolve()}", *args],
        cwd=paths.repository,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RootEffectError(result.stderr[-300:] or "root effect Git probe failed")
    return result.stdout.strip()


def _write_state(paths: RootEffectPaths, request: Mapping[str, Any], stage: str) -> None:
    request_identity = request.get("request_hash", request.get("preparation_hash"))
    if _SHA256.fullmatch(str(request_identity or "")) is None:
        raise RootEffectError("root effect state request identity is invalid")
    unsigned = {
        "schema": STATE_SCHEMA,
        "root_id": request["root_id"],
        "request_hash": request_identity,
        "stage": stage,
    }
    path = paths.request_root / f"{str(request['root_id']).removeprefix('coding:')}.state.json"
    _atomic(
        path,
        {**unsigned, "state_hash": _hash(unsigned)},
        replace=True,
        group_gid=_group_gid(paths),
        mode=0o640,
        owner_uid=paths.root_uid,
        directory_uid=paths.root_uid,
    )


def _runtime_canary(paths: RootEffectPaths, root_id: str) -> dict[str, Any]:
    """Run the real managed CLI twice against one copied durable journal."""

    with tempfile.TemporaryDirectory(
        prefix="coding-supervisor-canary-", dir=paths.request_root
    ) as temporary:
        root = Path(temporary)
        lifecycle = root / "lifecycles"
        lifecycle.mkdir(mode=0o2770)
        lifecycle.chmod(0o2770)
        source_store = LifecycleStore(
            paths.lifecycle_root, group_gid=_group_gid(paths)
        )
        source_record = source_store.get(root_id)
        if source_record is None:
            raise RootEffectError("canary lifecycle journal is absent")
        canary_store = LifecycleStore(
            lifecycle, group_gid=_group_gid(paths)
        )
        copied = dict(source_record)
        copied["state"] = "TECHNICALLY_COMPLETE"
        copied["publication"] = {
            **copied["publication"],
            "pending": False,
            "next_retry_at": None,
        }
        persisted = canary_store.put(copied)
        journal = canary_store.path(root_id)
        journal_before = journal.read_bytes()
        journal_sha256 = "sha256:" + hashlib.sha256(journal_before).hexdigest()
        config = root / "coding.json"
        source = json.loads(paths.coding_config.read_text(encoding="utf-8"))
        source["coding"]["lifecycle_root"] = str(lifecycle)
        source["coding"]["root_effect_root"] = str(root / "effects")
        config.write_text(json.dumps(source, sort_keys=True), encoding="utf-8")
        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(
                paths.runtime_root / "current" / "src"
            ),
        }
        if os.geteuid() != 0:
            env["TGW_CODING_DISPOSABLE_CANARY_GID"] = str(os.getegid())
        evidence = []
        for attempt in ("disconnect", "restart"):
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tgw.development.coding_lifecycle",
                    "--config",
                    str(config),
                    "--managed",
                    "--once",
                ],
                env=env,
                check=False,
                text=True,
                capture_output=True,
                timeout=30,
            )
            if completed.returncode:
                raise RootEffectError(
                    f"managed-supervisor {attempt} canary failed: {completed.stderr[-300:]}"
                )
            if (
                journal.read_bytes() != journal_before
                or canary_store.get(root_id) != persisted
            ):
                raise RootEffectError(
                    f"managed-supervisor {attempt} rewrote the terminal journal"
                )
            evidence.append(
                {
                    "phase": attempt,
                    "returncode": completed.returncode,
                    "root_id": root_id,
                    "journal_sha256": journal_sha256,
                    "output_sha256": "sha256:"
                    + hashlib.sha256(completed.stdout.encode()).hexdigest(),
                }
            )
    unsigned = {
        "schema": "tgw-local-coding-disconnect-restart-canary/v1",
        "disposable": True,
        "phases": evidence,
    }
    return {**unsigned, "canary_hash": _hash(unsigned)}


def _default_effects(
    paths: RootEffectPaths, request: Mapping[str, Any]
) -> dict[str, Any]:
    protected_review = request.get("_protected_review")
    if (
        not isinstance(protected_review, Mapping)
        or set(protected_review)
        != {
            "schema",
            "role",
            "candidate_commit",
            "candidate_tree",
            "plan_commit",
            "governed_bundle_hash",
            "candidate_receipt_hash",
            "role_receipt_hash",
            "execution_hash",
            "protected_review_hash",
        }
        or protected_review.get("schema")
        != "tgw-local-coding-protected-review-evidence/v1"
        or protected_review.get("role") != "independent-review"
        or protected_review.get("candidate_commit")
        != request.get("candidate_commit")
        or protected_review.get("candidate_tree") != request.get("candidate_tree")
        or protected_review.get("plan_commit") != request.get("plan_commit")
        or any(
            _SHA256.fullmatch(str(protected_review.get(field, ""))) is None
            for field in (
                "governed_bundle_hash",
                "candidate_receipt_hash",
                "role_receipt_hash",
                "execution_hash",
                "protected_review_hash",
            )
        )
        or protected_review.get("protected_review_hash")
        != _hash(
            {
                key: value
                for key, value in protected_review.items()
                if key != "protected_review_hash"
            }
        )
    ):
        raise RootEffectError(
            "root effect requires exact protected governed-review evidence"
        )
    if (
        _git(paths, "status", "--porcelain=v1")
        or _git(paths, "rev-parse", "HEAD") != request["candidate_commit"]
        or _git(paths, "rev-parse", "HEAD^{tree}") != request["candidate_tree"]
    ):
        raise RootEffectError("root effect requires the exact clean canonical candidate")
    _safe_root(
        paths.request_root,
        group_gid=_group_gid(paths),
        root_uid=paths.root_uid,
    )
    archive = paths.request_root / f"{request['candidate_commit']}.tar"
    if not _trusted_root_file(paths, archive, mode=0o440):
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=f".{request['candidate_commit']}.",
            suffix=".tar.tmp",
            dir=paths.request_root,
        )
        os.close(descriptor)
        temporary = Path(raw_temporary)
        try:
            completed = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={paths.repository.resolve()}",
                    "archive",
                    "--format=tar",
                    f"--output={temporary}",
                    str(request["candidate_commit"]),
                ],
                cwd=paths.repository,
                check=False,
                text=True,
                capture_output=True,
            )
            if completed.returncode:
                raise RootEffectError(
                    completed.stderr[-300:] or "Git archive failed"
                )
            os.chown(temporary, paths.root_uid, _group_gid(paths))
            os.chmod(temporary, 0o440)
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)
    archive_hash = _file_hash(archive).removeprefix("sha256:")
    manifest = materialize(
        paths.runtime_root,
        archive,
        generation=str(request["candidate_commit"]),
        commit=str(request["candidate_commit"]),
        tree=str(request["candidate_tree"]),
        archive_sha256=archive_hash,
    )
    verification = verify(paths.runtime_root, str(request["candidate_commit"]))
    materialization = {
        "schema": "tgw-local-coding-materialization/v1",
        "request_hash": request["request_hash"],
        "manifest_sha256": _hash(manifest),
        "archive_sha256": "sha256:" + archive_hash,
        "verification": verification,
    }
    _write_state(paths, request, "materialized")

    previous = current_generation(paths.runtime_root)
    operation = str(request["root_id"]).removeprefix("coding:")[:32]
    operation_id = f"coding-{operation}"
    if previous == request["candidate_commit"]:
        selection_path = paths.runtime_root / "receipts" / f"{operation_id}.json"
        try:
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RootEffectError(
                "selected lifecycle runtime lacks its exact selection receipt"
            ) from exc
        if (
            selection.get("state") != "completed"
            or selection.get("operation_id") != operation_id
            or selection.get("selected_generation") != request["candidate_commit"]
            or selection.get("evidence_identity")
            != {
                "request_hash": request["request_hash"],
                "protected_review_hash": protected_review[
                    "protected_review_hash"
                ],
                "governed_bundle_hash": protected_review[
                    "governed_bundle_hash"
                ],
            }
        ):
            raise RootEffectError("selected lifecycle runtime receipt differs")
    else:
        selection = select_fixed_local(
            paths.runtime_root,
            str(request["candidate_commit"]),
            expected_current=previous,
            operation_id=operation_id,
            evidence_validator=lambda selected: (
                None
                if selected.get("commit") == request["candidate_commit"]
                and selected.get("git_tree") == request["candidate_tree"]
                else (_ for _ in ()).throw(
                    RootEffectError("selected release differs")
                )
            ),
            evidence_identity={
                "request_hash": request["request_hash"],
                "protected_review_hash": protected_review[
                    "protected_review_hash"
                ],
                "governed_bundle_hash": protected_review[
                    "governed_bundle_hash"
                ],
            },
        )
    _write_state(paths, request, "selected")

    from tgw import doctor_cli

    doctor_paths = doctor_cli.DoctorPaths(
        repository=paths.repository,
        coding_config=paths.coding_config,
        runtime_root=paths.runtime_root,
    )
    workers = doctor_cli.repair_workers(
        doctor_paths, desired_commit=str(request["candidate_commit"])
    )
    _write_state(paths, request, "workers-restarted")
    canary = _runtime_canary(paths, str(request["root_id"]))
    _write_state(paths, request, "verified")
    return {
        "materialization": materialization,
        "selection": selection,
        "workers": workers,
        "live_verification": canary,
    }


def process_request(
    paths: RootEffectPaths,
    request_value: object,
    *,
    effects: Callable[[RootEffectPaths, Mapping[str, Any]], Mapping[str, Any]] = _default_effects,
    review_verifier: Callable[
        [RootEffectPaths, Mapping[str, Any], Mapping[str, Any]],
        Mapping[str, Any],
    ] = verify_protected_review_evidence,
    store: LifecycleStore | None = None,
) -> dict[str, Any]:
    """Execute or recover one exact request and publish one immutable response."""

    journal = store or LifecycleStore(
        paths.lifecycle_root, group_gid=_group_gid(paths)
    )
    request, record = validate_request(request_value, store=journal)
    prior = read_response(paths, request)
    if prior is not None:
        return prior
    protected_review = dict(review_verifier(paths, request, record))
    result = dict(
        effects(paths, {**request, "_protected_review": protected_review})
    )
    required = {"materialization", "selection", "workers", "live_verification"}
    if set(result) != required or not all(isinstance(result[key], Mapping) for key in required):
        raise RootEffectError("root effect state machine returned incomplete evidence")
    result_with_review = {**result, "protected_review": protected_review}
    hashes = {
        key: _hash(result_with_review[key]) for key in sorted(result_with_review)
    }
    technical_unsigned = {
        "schema": "tgw-local-coding-technical-result/v1",
        "root_id": request["root_id"],
        "binding_hash": request["binding_hash"],
        "candidate_commit": request["candidate_commit"],
        "candidate_tree": request["candidate_tree"],
        "request_hash": request["request_hash"],
        "receipt_hashes": hashes,
    }
    technical_hash = _hash(technical_unsigned)
    unsigned = {
        "schema": RESPONSE_SCHEMA,
        "status": "PASS",
        "root_id": request["root_id"],
        "binding_hash": request["binding_hash"],
        "request_hash": request["request_hash"],
        "candidate_commit": request["candidate_commit"],
        "candidate_tree": request["candidate_tree"],
        "protected_review_receipt_hash": hashes["protected_review"],
        "governed_review_bundle_hash": protected_review[
            "governed_bundle_hash"
        ],
        "materialization_receipt_hash": hashes["materialization"],
        "selection_receipt_hash": hashes["selection"],
        "workers_receipt_hash": hashes["workers"],
        "live_verification_receipt_hash": hashes["live_verification"],
        "technical_result_hash": technical_hash,
        "receipts": result_with_review,
    }
    response = {**unsigned, "response_hash": _hash(unsigned)}
    _atomic(
        response_path(paths, str(request["root_id"])),
        response,
        replace=True,
        group_gid=_group_gid(paths),
        mode=0o640,
        owner_uid=paths.root_uid,
        directory_uid=paths.root_uid,
    )
    return response


def validate_projection_request(
    value: object, *, store: LifecycleStore
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise RootEffectError("Context projection request is not an object")
    request = dict(value)
    expected = {
        "schema",
        "root_id",
        "binding_hash",
        "plan_commit",
        "solution_hash",
        "closure_hash",
        "source_commit",
        "source_tree",
        "card_idempotency_key",
        "candidate_commit",
        "candidate_tree",
        "candidate_receipt_hash",
        "review_receipt_hash",
        "integration_receipt_hash",
        "materialization_receipt_hash",
        "live_verification_receipt_hash",
        "technical_result_hash",
        "result_hash",
        "projection_hash",
    }
    unsigned = {
        key: item for key, item in request.items() if key != "projection_hash"
    }
    if (
        set(request) != expected
        or request.get("schema") != PROJECTION_SCHEMA
        or request.get("projection_hash") != _hash(unsigned)
        or _ROOT.fullmatch(str(request.get("root_id", ""))) is None
        or _COMMIT.fullmatch(str(request.get("plan_commit", ""))) is None
        or _COMMIT.fullmatch(str(request.get("source_commit", ""))) is None
        or _COMMIT.fullmatch(str(request.get("source_tree", ""))) is None
        or _COMMIT.fullmatch(str(request.get("candidate_commit", ""))) is None
        or _COMMIT.fullmatch(str(request.get("candidate_tree", ""))) is None
        or any(
            _SHA256.fullmatch(str(request.get(field, ""))) is None
            for field in expected
            - {
                "schema",
                "root_id",
                "plan_commit",
                "source_commit",
                "source_tree",
                "candidate_commit",
                "candidate_tree",
            }
        )
    ):
        raise RootEffectError("Context projection request schema/hash is invalid")
    record = store.get(str(request["root_id"]))
    if record is None or build_projection_request(record) != request:
        raise RootEffectError("Context projection differs from lifecycle terminal state")
    return request, record


def _project_terminal_task(
    paths: RootEffectPaths, request: Mapping[str, Any]
) -> str:
    """CAS one compact terminal orientation into the non-live task input."""

    from tgw import doctor_cli

    surface = doctor_cli._surface_snapshot(paths.context_task)
    task = doctor_cli._json_from_surface(paths.context_task, surface)
    plan = task.get("plan")
    implementation = task.get("implementation")
    development = (
        implementation.get("development_source")
        if isinstance(implementation, Mapping)
        else None
    )
    if (
        task.get("schema") != "tgw-current-task/v1"
        or not isinstance(plan, Mapping)
        or plan.get("approved_commit") != request["plan_commit"]
        or not isinstance(implementation, Mapping)
        or not isinstance(development, Mapping)
        or development.get("commit")
        not in {request["source_commit"], request["candidate_commit"]}
    ):
        raise RootEffectError(
            "Context task cannot be projected from the exact lifecycle binding"
        )
    terminal = {
        "schema": "tgw-local-coding-context-terminal-projection/v1",
        "root_id": request["root_id"],
        "binding_hash": request["binding_hash"],
        "solution_hash": request["solution_hash"],
        "closure_hash": request["closure_hash"],
        "card_idempotency_key": request["card_idempotency_key"],
        "candidate_commit": request["candidate_commit"],
        "candidate_tree": request["candidate_tree"],
        "result_hash": request["result_hash"],
        "technical_result_hash": request["technical_result_hash"],
        "operator_acceptance": "PENDING",
    }
    existing = implementation.get("coding_lifecycle_result")
    if development.get("commit") == request["candidate_commit"]:
        if existing != terminal:
            raise RootEffectError(
                "Context task candidate projection belongs to another lifecycle"
            )
        return _file_hash(paths.context_task)
    projected = dict(task)
    projected_implementation = dict(implementation)
    projected_development = dict(development)
    projected_development["commit"] = request["candidate_commit"]
    if "tree" in projected_development:
        projected_development["tree"] = request["candidate_tree"]
    projected_implementation["development_source"] = projected_development
    coding_workflow = projected_implementation.get("coding_workflow")
    if isinstance(coding_workflow, Mapping):
        projected_workflow = dict(coding_workflow)
        if "commit" in projected_workflow:
            projected_workflow["commit"] = request["candidate_commit"]
        projected_implementation["coding_workflow"] = projected_workflow
    projected_implementation["coding_lifecycle_result"] = terminal
    projected["implementation"] = projected_implementation
    projected["updated_at"] = datetime.now(timezone.utc).isoformat()
    doctor_cli._cas_regular_file(
        paths.context_task,
        surface,
        doctor_cli._json_bytes(projected),
        mode=surface["mode"],
        uid=surface["uid"],
        gid=surface["gid"],
    )
    return _file_hash(paths.context_task)


def _publish_context(
    paths: RootEffectPaths, request: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Invoke the existing local Doctor publisher and bind its returned receipt."""

    from tgw import doctor_cli

    task_file_sha256 = _project_terminal_task(paths, request)
    result = doctor_cli.repair_context(
        doctor_cli.DoctorPaths(
            repository=paths.repository,
            coding_config=paths.coding_config,
            runtime_root=paths.runtime_root,
        )
    )
    receipt_path_value = result.get("receipt")
    if not isinstance(receipt_path_value, str):
        raise RootEffectError("Context publisher returned no exact receipt path")
    receipt_path = Path(receipt_path_value)
    receipt = _load_exact(receipt_path)
    snapshot = receipt.get("after", {}).get("snapshot", {})
    if (
        receipt.get("schema") != "tgw-local-doctor-repair-receipt/v1"
        or receipt.get("operation") != "context"
        or receipt.get("error") is not None
        or snapshot.get("plan_commit") != request["plan_commit"]
        or snapshot.get("source_commit") != request["candidate_commit"]
        or snapshot.get("source_tree") != request["candidate_tree"]
    ):
        raise RootEffectError("Context publisher receipt differs from terminal result")
    return {
        "path": str(receipt_path),
        "file_sha256": _file_hash(receipt_path),
        "receipt_sha256": receipt.get("receipt_sha256"),
        "task_file_sha256": task_file_sha256,
    }


def process_projection(
    paths: RootEffectPaths,
    request_value: object,
    *,
    publisher: Callable[
        [RootEffectPaths, Mapping[str, Any]], Mapping[str, Any]
    ] = _publish_context,
    store: LifecycleStore | None = None,
) -> dict[str, Any]:
    journal = store or LifecycleStore(
        paths.lifecycle_root, group_gid=_group_gid(paths)
    )
    request, _record = validate_projection_request(request_value, store=journal)
    prior = read_projection_response(paths, request)
    if prior is not None:
        return prior
    evidence = dict(publisher(paths, request))
    if (
        set(evidence)
        != {"path", "file_sha256", "receipt_sha256", "task_file_sha256"}
        or _SHA256.fullmatch(str(evidence.get("file_sha256", ""))) is None
        or _SHA256.fullmatch(str(evidence.get("receipt_sha256", ""))) is None
        or _SHA256.fullmatch(str(evidence.get("task_file_sha256", ""))) is None
    ):
        raise RootEffectError("Context publisher evidence is incomplete")
    unsigned = {
        "schema": PROJECTION_RESPONSE_SCHEMA,
        "status": "PUBLISHED",
        "root_id": request["root_id"],
        "binding_hash": request["binding_hash"],
        "projection_hash": request["projection_hash"],
        "result_hash": request["result_hash"],
        "context_receipt_path": evidence["path"],
        "context_receipt_file_sha256": evidence["file_sha256"],
        "context_receipt_sha256": evidence["receipt_sha256"],
        "context_task_file_sha256": evidence["task_file_sha256"],
    }
    response = {**unsigned, "response_hash": _hash(unsigned)}
    _atomic(
        projection_response_path(paths, str(request["root_id"])),
        response,
        replace=True,
        group_gid=_group_gid(paths),
        mode=0o640,
        owner_uid=paths.root_uid,
        directory_uid=paths.root_uid,
    )
    return response


def _projection_retry_path(paths: RootEffectPaths, root_id: str) -> Path:
    return paths.request_root / (
        f"{root_id.removeprefix('coding:')}.projection-retry.json"
    )


def _projection_is_due(paths: RootEffectPaths, request: Mapping[str, Any]) -> bool:
    path = _projection_retry_path(paths, str(request["root_id"]))
    retry = _root_document_or_absent(paths, path)
    if retry is None:
        return True
    unsigned = {key: item for key, item in retry.items() if key != "retry_hash"}
    if (
        retry.get("schema") != "tgw-local-coding-context-retry/v1"
        or retry.get("projection_hash") != request.get("projection_hash")
        or retry.get("retry_hash") != _hash(unsigned)
    ):
        raise RootEffectError("Context projection retry state is invalid")
    try:
        due = datetime.fromisoformat(str(retry["next_retry_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise RootEffectError("Context projection retry time is invalid") from exc
    if due.tzinfo is None or due.utcoffset() is None:
        raise RootEffectError("Context projection retry time is not timezone-aware")
    return datetime.now(timezone.utc) >= due


def _defer_projection(
    paths: RootEffectPaths, request: Mapping[str, Any], reason: str
) -> None:
    path = _projection_retry_path(paths, str(request["root_id"]))
    attempts = 1
    prior = _root_document_or_absent(paths, path)
    if prior is not None:
        if prior.get("projection_hash") == request.get("projection_hash"):
            attempts = int(prior.get("attempts", 0)) + 1
    delay = min(900, 30 * (2 ** min(attempts - 1, 5)))
    unsigned = {
        "schema": "tgw-local-coding-context-retry/v1",
        "root_id": request["root_id"],
        "projection_hash": request["projection_hash"],
        "attempts": attempts,
        "last_error": reason[-500:],
        "next_retry_at": (
            datetime.now(timezone.utc) + timedelta(seconds=delay)
        ).isoformat(),
    }
    _atomic(
        path,
        {**unsigned, "retry_hash": _hash(unsigned)},
        replace=True,
        group_gid=_group_gid(paths),
        mode=0o640,
        owner_uid=paths.root_uid,
        directory_uid=paths.root_uid,
    )


def _refusal_path(paths: RootEffectPaths, request_file: Path) -> Path:
    stem = request_file.name.removesuffix(".json")
    return paths.request_root / f"{stem}.refusal.json"


def _refuse_invalid_file(
    paths: RootEffectPaths, request_file: Path, reason: str
) -> None:
    observed = request_file.lstat()
    unsigned = {
        "schema": "tgw-local-coding-root-effect-refusal/v1",
        "request_file": request_file.name,
        "device": observed.st_dev,
        "inode": observed.st_ino,
        "mode": stat.S_IFMT(observed.st_mode) | stat.S_IMODE(observed.st_mode),
        "link_count": observed.st_nlink,
        "reason": reason[-500:],
    }
    _atomic(
        _refusal_path(paths, request_file),
        {**unsigned, "refusal_hash": _hash(unsigned)},
        replace=True,
        group_gid=_group_gid(paths),
        mode=0o640,
        owner_uid=paths.root_uid,
        directory_uid=paths.root_uid,
    )


def _refusal_applies(
    paths: RootEffectPaths, refusal_file: Path, request_file: Path
) -> bool:
    refusal = _root_document_or_absent(paths, refusal_file)
    if refusal is None:
        return False
    unsigned = {key: item for key, item in refusal.items() if key != "refusal_hash"}
    try:
        observed = request_file.lstat()
    except FileNotFoundError:
        return False
    if (
        refusal.get("schema")
        != "tgw-local-coding-root-effect-refusal/v1"
        or refusal.get("request_file") != request_file.name
        or refusal.get("refusal_hash") != _hash(unsigned)
    ):
        raise RootEffectError("root effect refusal record is invalid")
    return (
        refusal.get("device") == observed.st_dev
        and refusal.get("inode") == observed.st_ino
        and refusal.get("mode")
        == stat.S_IFMT(observed.st_mode) | stat.S_IMODE(observed.st_mode)
        and refusal.get("link_count") == observed.st_nlink
    )


def consume_once(paths: RootEffectPaths) -> int:
    _safe_root(
        paths.request_root,
        group_gid=_group_gid(paths),
        root_uid=paths.root_uid,
    )
    store = LifecycleStore(paths.lifecycle_root, group_gid=_group_gid(paths))
    processed = 0
    for path in sorted(
        paths.request_root.glob("*.review-preparation-request.json")
    ):
        refusal = _refusal_path(paths, path)
        if _refusal_applies(paths, refusal, path):
            continue
        try:
            value = _load_exact(
                path,
                expected_gid=_group_gid(paths),
                expected_mode=0o660,
            )
            request, _record = validate_review_preparation_request(
                value, store=store
            )
        except (LifecycleError, RootEffectError, OSError, ValueError) as exc:
            _refuse_invalid_file(paths, path, str(exc))
            continue
        try:
            if read_review_preparation_response(paths, request) is None:
                process_review_preparation(paths, request, store=store)
                processed += 1
        except (
            KeyError,
            LifecycleError,
            RootEffectError,
            OSError,
            ReviewRunnerError,
            TypeError,
            ValueError,
        ):
            _write_state(paths, request, "protected-review-preparation-held")
    for path in sorted(paths.request_root.glob("*.request.json")):
        if path.name.endswith(".review-preparation-request.json"):
            continue
        refusal = _refusal_path(paths, path)
        if _refusal_applies(paths, refusal, path):
            continue
        try:
            value = _load_exact(
                path,
                expected_gid=_group_gid(paths),
                expected_mode=0o660,
            )
            request, _record = validate_request(value, store=store)
        except (LifecycleError, RootEffectError, OSError, ValueError) as exc:
            _refuse_invalid_file(paths, path, str(exc))
            continue
        if read_response(paths, request) is not None:
            continue
        try:
            process_request(paths, request, store=store)
        except ProtectedReviewEvidenceError:
            # A valid ordinary-user trigger is not review authority. Missing,
            # stale, or mismatched protected evidence remains safely retryable
            # and cannot prevent other exact lifecycle requests from advancing.
            _write_state(paths, request, "protected-review-held")
        else:
            processed += 1
    for path in sorted(paths.request_root.glob("*.projection-request.json")):
        refusal = _refusal_path(paths, path)
        if _refusal_applies(paths, refusal, path):
            continue
        try:
            value = _load_exact(
                path,
                expected_gid=_group_gid(paths),
                expected_mode=0o660,
            )
            request, _record = validate_projection_request(value, store=store)
        except (LifecycleError, RootEffectError, OSError, ValueError) as exc:
            _refuse_invalid_file(paths, path, str(exc))
            continue
        if (
            read_projection_response(paths, request) is not None
            or not _projection_is_due(paths, request)
        ):
            continue
        try:
            process_projection(paths, request, store=store)
        except (LifecycleError, RootEffectError, OSError, RuntimeError) as exc:
            _defer_projection(paths, request, str(exc))
        else:
            processed += 1
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-coding-root-effect")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("coding root effect consumer requires root")
    paths = RootEffectPaths.from_config(args.config)
    while True:
        consume_once(paths)
        if args.once:
            return 0
        time.sleep(max(1.0, args.poll_interval))


if __name__ == "__main__":
    raise SystemExit(main())
