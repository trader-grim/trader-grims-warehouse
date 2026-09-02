"""Fixed-schema local materializer for the Unix-user coding workflow.

Ordinary ``tgw-coders`` publish one self-hashed request derived from the
read-only lifecycle journal. The consumer runs as the ordinary ``db`` account,
never as root, and accepts no command, provider, approval, admission, or remote
effect fields. It can only materialize and select the local development
runtime. A static systemd path unit performs the fixed worker restart.
"""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import pwd
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

from tgw.development.coding_lifecycle import LifecycleError, LifecycleStore
from tgw.protected_git import (
    protected_git_command,
    protected_git_environment,
    write_exact_tree_archive,
)
from tgw.release_installer import (
    _select as select_fixed_local,  # the fixed CAS primitive; never admission
)
from tgw.release_installer import (
    current_generation,
    materialize,
    promote_release_ownership,
    verify,
)

REQUEST_SCHEMA = "tgw-local-coding-root-effect-request/v1"
RESPONSE_SCHEMA = "tgw-local-coding-root-effect-response/v1"
STATE_SCHEMA = "tgw-local-coding-root-effect-state/v1"
PROJECTION_SCHEMA = "tgw-local-coding-context-projection-request/v1"
PROJECTION_RESPONSE_SCHEMA = "tgw-local-coding-context-projection-response/v1"
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


class RestartPending(RuntimeError):
    """The fixed root-owned restart unit has not acknowledged cutover yet."""


@dataclass(frozen=True)
class RootEffectPaths:
    request_root: Path
    lifecycle_root: Path
    repository: Path
    runtime_root: Path
    coding_config: Path
    context_task: Path = Path(
        "/opt/TGW/tgw-lib/context-input/current-task.json"
    )
    context_cursor: Path = Path(
        "/opt/TGW/tgw-lib/context-input/plan-cycle-cursor.json"
    )
    context_pending: Path = Path(
        "/opt/TGW/tgw-lib/context-input/current-context.pending.json"
    )
    context_snapshot: Path = Path(
        "/opt/TGW/tgw-lib/config/tgw-context-current.json"
    )
    restart_ack: Path = Path("/run/tgw-coding-runtime-restart/complete")
    coding_bootstrap: Path = Path("/usr/local/sbin/tgw-coding-bootstrap")
    group_gid: int | None = None
    root_uid: int = os.geteuid()
    # The cold Doctor/Context launcher requires the selected release tree to be
    # this exact owner.  A lifecycle materialization runs unprivileged, so the
    # promotion is emitted as a bounded root-effect the pinned root bootstrap
    # applies, and _restart_acknowledged blocks until the tree is root-owned.
    context_install_uid: int = 0
    context_install_gid: int = 0

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
    """Derive the unprivileged materialization request from completed work."""

    binding = record.get("binding", {})
    candidate = record.get("effects", {}).get("candidate", {}).get("receipt", {})
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


def _safe_root(root: Path, *, group_gid: int, root_uid: int = 0) -> None:
    if not root.exists():
        raise RootEffectError("root effect directory has not been provisioned by Doctor")
    state = root.stat(follow_symlinks=False)
    if (
        root.is_symlink()
        or not stat.S_ISDIR(state.st_mode)
        or state.st_uid != root_uid
        or state.st_gid != group_gid
        or stat.S_IMODE(state.st_mode) != 0o2750
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
            # Ignore a legacy foreign-owned artifact. Only the directory-owning
            # db materializer can replace or mint files in this parent.
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
        protected_git_command(paths.repository, *args),
        cwd=paths.repository,
        check=False,
        text=True,
        capture_output=True,
        env=dict(protected_git_environment()),
    )
    if result.returncode:
        raise RootEffectError(result.stderr[-300:] or "root effect Git probe failed")
    return result.stdout.strip()


def _write_state(paths: RootEffectPaths, request: Mapping[str, Any], stage: str) -> None:
    unsigned = {
        "schema": STATE_SCHEMA,
        "root_id": request["root_id"],
        "request_hash": request["request_hash"],
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


def _restart_request(paths: RootEffectPaths, request: Mapping[str, Any]) -> Path:
    """Publish one idempotent trigger consumed only by the fixed path unit."""

    path = paths.request_root / ".restart-request"
    value = {
        "schema": "tgw-local-coding-static-restart-request/v1",
        "candidate_commit": request["candidate_commit"],
    }
    try:
        existing = _load_exact(
            path,
            expected_uid=paths.root_uid,
            expected_gid=_group_gid(paths),
            expected_mode=0o640,
        )
    except RootEffectError:
        if path.exists() or path.is_symlink():
            raise
        existing = None
    if existing != value:
        _atomic(
            path,
            value,
            replace=True,
            group_gid=_group_gid(paths),
            mode=0o640,
            owner_uid=paths.root_uid,
            directory_uid=paths.root_uid,
        )
    return path


def _selected_release_ownership(
    paths: RootEffectPaths, candidate_commit: str
) -> dict[str, Any]:
    """Describe the exact ownership/mode of the selected release tree.

    ``materialize`` already lands 0555 directories and 0444/0555 files; the
    only thing an unprivileged lifecycle materialization cannot do is leave the
    tree ``context_install_uid:context_install_gid`` (root:root).  This returns
    that observation so callers can require the fixed root promotion.
    """

    release = paths.runtime_root / "releases" / candidate_commit
    if _COMMIT.fullmatch(candidate_commit) is None:
        raise RootEffectError("selected release identity is invalid")
    if release.is_symlink() or not release.is_dir():
        raise RootEffectError("selected release is not an immutable directory")
    want_uid = paths.context_install_uid
    want_gid = paths.context_install_gid
    unsafe: list[str] = []
    for path in (release, *sorted(release.rglob("*"))):
        observed = path.stat(follow_symlinks=False)
        relative = "." if path == release else str(path.relative_to(release))
        mode = stat.S_IMODE(observed.st_mode)
        if path.is_symlink():
            unsafe.append(relative + ":symlink")
            continue
        if observed.st_uid != want_uid or observed.st_gid != want_gid:
            unsafe.append(relative + ":owner")
        if observed.st_mode & 0o022:
            unsafe.append(relative + ":writable")
        if stat.S_ISDIR(observed.st_mode):
            if mode != 0o555:
                unsafe.append(relative + ":dir-mode")
        elif stat.S_ISREG(observed.st_mode):
            if mode not in (0o444, 0o555):
                unsafe.append(relative + ":file-mode")
            if observed.st_nlink != 1:
                unsafe.append(relative + ":link-count")
        else:
            unsafe.append(relative + ":special")
    return {
        "release": str(release),
        "expected_uid": want_uid,
        "expected_gid": want_gid,
        "root_owned_immutable": not unsafe,
        "unsafe": unsafe[:8],
    }


def _apply_release_ownership_via_bootstrap(
    paths: RootEffectPaths, candidate_commit: str
) -> dict[str, Any]:
    """Apply the bounded root-effect through the existing pinned root bootstrap.

    This is the same ``tgw-recovery`` sudoers pin the Doctor already prescribes
    for privileged coding repairs — it is invoked with a fixed argument shape
    and no sudo surface is widened.  The pinned root path re-verifies the exact
    Git tree, promotes ownership, and re-verifies, so the root:root immutability
    check is never bypassed.
    """

    bootstrap = paths.coding_bootstrap
    try:
        state = bootstrap.lstat()
    except OSError:
        return {"status": "pinned-bootstrap-unavailable", "path": str(bootstrap)}
    if bootstrap.is_symlink() or not stat.S_ISREG(state.st_mode) or state.st_uid != 0:
        return {"status": "pinned-bootstrap-untrusted", "path": str(bootstrap)}
    completed = subprocess.run(
        [
            "/usr/bin/sudo",
            "-n",
            str(bootstrap),
            "--commit",
            candidate_commit,
            "--repair",
            "release-ownership",
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=600,
    )
    return {
        "status": "invoked",
        "invocation": "sudo -n tgw-coding-bootstrap --repair release-ownership",
        "returncode": completed.returncode,
        "detail": (completed.stderr or completed.stdout)[-500:],
    }


def _ensure_selected_release_root_owned(
    paths: RootEffectPaths, request: Mapping[str, Any]
) -> dict[str, Any]:
    """Land the selected release ``root:root`` immutable for the cold Doctor.

    ``materialize`` already lands 0555 directories and 0444/0555 files, but an
    unprivileged lifecycle materialization leaves them owned by ``db``.  When
    this consumer already runs as ``context_install_uid`` the promotion happens
    inline; otherwise it emits a bounded root-effect request that the existing
    pinned root bootstrap applies.  Either way :func:`_restart_acknowledged`
    blocks until the tree is genuinely root-owned, so an ordinary lifecycle
    materialization never self-wounds the Doctor.
    """

    candidate_commit = str(request["candidate_commit"])
    ownership = _selected_release_ownership(paths, candidate_commit)
    if ownership["root_owned_immutable"]:
        return {"status": "already-root-owned", "ownership": ownership}
    if os.geteuid() == paths.context_install_uid:
        promotion = promote_release_ownership(
            paths.runtime_root,
            candidate_commit,
            uid=paths.context_install_uid,
            gid=paths.context_install_gid,
        )
        after = _selected_release_ownership(paths, candidate_commit)
        if not after["root_owned_immutable"]:
            raise RootEffectError(
                "inline release ownership promotion is incomplete: "
                + ",".join(after["unsafe"])
            )
        return {"status": "promoted-inline", "promotion": promotion, "ownership": after}
    outcome = _apply_release_ownership_via_bootstrap(paths, candidate_commit)
    return {
        "status": "delegated-to-pinned-bootstrap",
        "bootstrap": outcome,
        "ownership": _selected_release_ownership(paths, candidate_commit),
    }


def _restart_acknowledged(
    paths: RootEffectPaths, trigger: Path, *, candidate_commit: str
) -> dict[str, Any]:
    """Prove the static service completed after this exact trigger was written."""

    acknowledgement = paths.restart_ack
    try:
        trigger_state = trigger.stat(follow_symlinks=False)
        ack_state = acknowledgement.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise RestartPending("awaiting fixed coding-runtime restart acknowledgement") from exc
    if (
        acknowledgement.is_symlink()
        or not stat.S_ISREG(ack_state.st_mode)
        or ack_state.st_uid != 0
        or ack_state.st_gid != 0
        or stat.S_IMODE(ack_state.st_mode) != 0o444
        or ack_state.st_mtime_ns < trigger_state.st_mtime_ns
    ):
        raise RestartPending("fixed coding-runtime restart acknowledgement is stale")
    required = (
        "tgw-codex-implement-worker.service",
        "tgw-claude-review-worker.service",
        "tgw-controller-verify-worker.service",
        "tgw-coding-lifecycle-supervisor.service",
        "tgw-plan-render-local.service",
    )
    observed: dict[str, str] = {}
    for unit in required:
        completed = subprocess.run(
            ["/bin/systemctl", "is-active", unit],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
        state = completed.stdout.strip()
        observed[unit] = state
        if completed.returncode or state != "active":
            raise RestartPending(f"fixed restart has not made {unit} active")
    selected = current_generation(paths.runtime_root)
    if selected != candidate_commit:
        raise RestartPending("fixed restart acknowledgement belongs to another runtime")
    ownership = _selected_release_ownership(paths, candidate_commit)
    if not ownership["root_owned_immutable"]:
        raise RestartPending(
            "fixed restart has not promoted the selected release to root:root: "
            + ",".join(ownership["unsafe"])
        )
    unsigned = {
        "schema": "tgw-local-coding-static-restart-acknowledgement/v1",
        "candidate_commit": selected,
        "acknowledgement": str(acknowledgement),
        "acknowledgement_mtime_ns": ack_state.st_mtime_ns,
        "services": observed,
        "release_root_ownership": {
            "expected_uid": ownership["expected_uid"],
            "expected_gid": ownership["expected_gid"],
            "root_owned_immutable": ownership["root_owned_immutable"],
        },
    }
    return {**unsigned, "acknowledgement_hash": _hash(unsigned)}


def _default_effects(
    paths: RootEffectPaths, request: Mapping[str, Any]
) -> dict[str, Any]:
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
            try:
                write_exact_tree_archive(
                    paths.repository,
                    commit=str(request["candidate_commit"]),
                    tree=str(request["candidate_tree"]),
                    destination=temporary,
                )
            except ValueError as exc:
                raise RootEffectError(str(exc)) from exc
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
    evidence_identity = {
        "request_hash": request["request_hash"],
        "integration_receipt_hash": request["integration_receipt_hash"],
    }
    selection_path = paths.runtime_root / "receipts" / f"{operation_id}.json"
    if selection_path.exists():
        try:
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RootEffectError("lifecycle selection receipt is unreadable") from exc
        if (
            selection.get("state") != "completed"
            or selection.get("operation_id") != operation_id
            or selection.get("selected_generation") != request["candidate_commit"]
            or selection.get("evidence_identity") != evidence_identity
            or previous != request["candidate_commit"]
        ):
            raise RootEffectError("selected lifecycle runtime receipt differs")
    else:
        # Selecting the same generation is intentional.  It creates the exact
        # lifecycle-bound receipt after a first-install bootstrap selected the
        # bytes with its separate bootstrap operation identity.
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
            evidence_identity=evidence_identity,
        )
    _write_state(paths, request, "selected")
    _ensure_selected_release_root_owned(paths, request)
    _write_state(paths, request, "promoted")
    trigger = _restart_request(paths, request)
    workers = _restart_acknowledged(
        paths, trigger, candidate_commit=str(request["candidate_commit"])
    )
    canary = _runtime_canary(paths, str(request["root_id"]))
    _write_state(paths, request, "verified")
    return {
        "materialization": materialization,
        "selection": selection,
        "workers": workers,
        "live_verification": canary,
    }


def bootstrap_candidate(
    paths: RootEffectPaths, *, commit: str, tree: str
) -> dict[str, Any]:
    """Materialize/select one exact clean commit without Context or lifecycle state.

    This is the acyclic first-install primitive. It is intentionally available
    only to the ordinary ``db:tgw-coders`` account and accepts no command,
    provider, review, admission, Plan, or remote-effect input.
    """

    if _COMMIT.fullmatch(commit) is None or _COMMIT.fullmatch(tree) is None:
        raise RootEffectError("bootstrap commit/tree identity is invalid")
    if (
        _git(paths, "status", "--porcelain=v1")
        or _git(paths, "rev-parse", "HEAD") != commit
        or _git(paths, "rev-parse", "HEAD^{tree}") != tree
    ):
        raise RootEffectError("bootstrap requires the exact clean canonical source")
    _safe_root(
        paths.request_root,
        group_gid=_group_gid(paths),
        root_uid=paths.root_uid,
    )
    archive = paths.request_root / f"{commit}.tar"
    if not _trusted_root_file(paths, archive, mode=0o440):
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{commit}.", suffix=".tar.tmp", dir=paths.request_root
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            write_exact_tree_archive(
                paths.repository,
                commit=commit,
                tree=tree,
                destination=temporary,
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
        generation=commit,
        commit=commit,
        tree=tree,
        archive_sha256=archive_hash,
    )
    verification = verify(paths.runtime_root, commit)
    previous = current_generation(paths.runtime_root)
    if previous == commit:
        selection = {
            "state": "already-selected",
            "selected_generation": commit,
        }
    else:
        selection = select_fixed_local(
            paths.runtime_root,
            commit,
            expected_current=previous,
            operation_id=f"bootstrap-{commit[:32]}",
            evidence_validator=lambda selected: (
                None
                if selected.get("commit") == commit
                and selected.get("git_tree") == tree
                else (_ for _ in ()).throw(
                    RootEffectError("bootstrap release differs")
                )
            ),
            evidence_identity={"commit": commit, "tree": tree},
        )
    unsigned = {
        "schema": "tgw-local-coding-bootstrap-materialization/v1",
        "actor": pwd.getpwuid(os.geteuid()).pw_name,
        "commit": commit,
        "tree": tree,
        "previous_generation": previous,
        "manifest_sha256": _hash(manifest),
        "archive_sha256": "sha256:" + archive_hash,
        "verification": verification,
        "selection": selection,
    }
    return {**unsigned, "receipt_hash": _hash(unsigned)}


def process_request(
    paths: RootEffectPaths,
    request_value: object,
    *,
    effects: Callable[[RootEffectPaths, Mapping[str, Any]], Mapping[str, Any]] = _default_effects,
    store: LifecycleStore | None = None,
) -> dict[str, Any]:
    """Execute or recover one exact request and publish one immutable response."""

    journal = store or LifecycleStore(
        paths.lifecycle_root, group_gid=_group_gid(paths)
    )
    request, _record = validate_request(request_value, store=journal)
    prior = read_response(paths, request)
    if prior is not None:
        return prior
    result = dict(effects(paths, request))
    required = {"materialization", "selection", "workers", "live_verification"}
    if set(result) != required or not all(isinstance(result[key], Mapping) for key in required):
        raise RootEffectError("root effect state machine returned incomplete evidence")
    hashes = {key: _hash(result[key]) for key in sorted(result)}
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
        "materialization_receipt_hash": hashes["materialization"],
        "selection_receipt_hash": hashes["selection"],
        "workers_receipt_hash": hashes["workers"],
        "live_verification_receipt_hash": hashes["live_verification"],
        "technical_result_hash": technical_hash,
        "receipts": result,
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
        or (
            development.get("commit"), development.get("tree")
        )
        not in {
            (request["source_commit"], request["source_tree"]),
            (request["candidate_commit"], request["candidate_tree"]),
        }
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
    """Publish orientation as db, then verify the fixed root promotion."""

    from tgw import doctor_cli

    # Bind both mutable inputs before changing either one.  Each later CAS
    # still detects a concurrent replacement, while this preflight prevents a
    # stale cursor from leaving a partially advanced task projection.
    cursor_surface = doctor_cli._surface_snapshot(paths.context_cursor)
    cursor = doctor_cli._json_from_surface(paths.context_cursor, cursor_surface)
    if (
        cursor.get("plan_commit") != request["plan_commit"]
        or (cursor.get("source_commit"), cursor.get("source_tree"))
        not in {
            (request["source_commit"], request["source_tree"]),
            (request["candidate_commit"], request["candidate_tree"]),
        }
    ):
        raise RootEffectError(
            "Context cursor differs from the exact lifecycle Plan/source binding"
        )
    task_file_sha256 = _project_terminal_task(paths, request)
    cursor["source_commit"] = request["candidate_commit"]
    cursor["source_tree"] = request["candidate_tree"]
    cursor["updated_at"] = datetime.now(timezone.utc).isoformat()
    doctor_cli._cas_regular_file(
        paths.context_cursor,
        cursor_surface,
        doctor_cli._json_bytes(cursor),
        mode=cursor_surface["mode"],
        uid=cursor_surface["uid"],
        gid=cursor_surface["gid"],
    )
    publisher = paths.runtime_root / "current/scripts/tgw_context_publish.py"
    completed = subprocess.run(
        [
            str(publisher),
            "--task",
            str(paths.context_task),
            "--cursor",
            str(paths.context_cursor),
            "--output",
            str(paths.context_pending),
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
        env={
            "HOME": "/home/db",
            "LANG": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    if completed.returncode:
        raise RootEffectError(
            completed.stderr[-500:] or "ordinary Context publisher failed"
        )
    pending_hash = _file_hash(paths.context_pending)
    if not paths.context_snapshot.is_file() or _file_hash(
        paths.context_snapshot
    ) != pending_hash:
        raise RestartPending("awaiting fixed Context snapshot promotion")
    snapshot = _load_exact(paths.context_snapshot)
    if (
        snapshot.get("plan_commit") != request["plan_commit"]
        or snapshot.get("source_commit") != request["candidate_commit"]
        or snapshot.get("source_tree") != request["candidate_tree"]
    ):
        raise RootEffectError("promoted Context snapshot differs from terminal result")
    receipt_unsigned = {
        "schema": "tgw-local-coding-context-publication/v1",
        "root_id": request["root_id"],
        "projection_hash": request["projection_hash"],
        "task_file_sha256": task_file_sha256,
        "cursor_file_sha256": _file_hash(paths.context_cursor),
        "snapshot_file_sha256": pending_hash,
    }
    receipt = {
        **receipt_unsigned,
        "receipt_sha256": _hash(receipt_unsigned),
    }
    receipt_path = paths.request_root / (
        str(request["root_id"]).removeprefix("coding:")
        + ".context-receipt.json"
    )
    _atomic(
        receipt_path,
        receipt,
        replace=True,
        group_gid=_group_gid(paths),
        mode=0o640,
        owner_uid=paths.root_uid,
        directory_uid=paths.root_uid,
    )
    return {
        "path": str(receipt_path),
        "file_sha256": _file_hash(receipt_path),
        "receipt_sha256": receipt["receipt_sha256"],
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
    content_sha256 = _file_hash(request_file)
    unsigned = {
        "schema": "tgw-local-coding-root-effect-refusal/v1",
        "request_file": request_file.name,
        "device": observed.st_dev,
        "inode": observed.st_ino,
        "mode": stat.S_IFMT(observed.st_mode) | stat.S_IMODE(observed.st_mode),
        "link_count": observed.st_nlink,
        "content_sha256": content_sha256,
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
        and refusal.get("content_sha256") == _file_hash(request_file)
    )


def consume_once(paths: RootEffectPaths) -> int:
    _safe_root(
        paths.request_root,
        group_gid=_group_gid(paths),
        root_uid=paths.root_uid,
    )
    store = LifecycleStore(paths.lifecycle_root, group_gid=_group_gid(paths))
    processed = 0
    for path in sorted(paths.request_root.glob("*.request.json")):
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
        except RestartPending:
            continue
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
    parser.add_argument("--bootstrap-commit")
    parser.add_argument("--bootstrap-tree")
    args = parser.parse_args()
    actor = pwd.getpwuid(os.geteuid()).pw_name
    coding_gid = grp.getgrnam("tgw-coders").gr_gid
    if actor != "db" or coding_gid not in ({os.getegid()} | set(os.getgroups())):
        raise SystemExit("coding materializer requires db in tgw-coders")
    paths = RootEffectPaths.from_config(args.config)
    if args.bootstrap_commit is not None or args.bootstrap_tree is not None:
        if not args.bootstrap_commit or not args.bootstrap_tree:
            raise SystemExit("bootstrap requires both commit and tree")
        print(
            json.dumps(
                bootstrap_candidate(
                    paths,
                    commit=args.bootstrap_commit,
                    tree=args.bootstrap_tree,
                ),
                sort_keys=True,
            )
        )
        return 0
    while True:
        consume_once(paths)
        if args.once:
            return 0
        time.sleep(max(1.0, args.poll_interval))


if __name__ == "__main__":
    raise SystemExit(main())
