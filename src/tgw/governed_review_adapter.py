"""Root-captured, provider-neutral adapter for the established ``tgw-review`` path.

This is not a model reviewer.  It launches the selected qualified harness with
the canonical provider-neutral review skill/MCP context and retains an
admission-verifiable execution record.  Any qualified harness can use the same
contract. QES is a separate optional execution path.
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tgw.review_runner import ReviewRunnerError, _validate_report, snapshot_hash

EXECUTION_SCHEMA = "tgw-governed-review-execution/v1"
IDENTITY_SCHEMA = "tgw-governed-review-provider-identity/v1"
_SHA256_PREFIX = "sha256:"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return _SHA256_PREFIX + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return _SHA256_PREFIX + digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    value = path.stat(follow_symlinks=False)
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "uid": value.st_uid,
        "gid": value.st_gid,
        "mode": stat.S_IMODE(value.st_mode),
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def _command_identity(path: Path) -> dict[str, Any]:
    value = path.lstat()
    return {
        "device": value.st_dev, "inode": value.st_ino, "uid": value.st_uid,
        "gid": value.st_gid, "mode": stat.S_IMODE(value.st_mode),
        "nlink": value.st_nlink, "size": value.st_size, "mtime_ns": value.st_mtime_ns,
        "is_symlink": path.is_symlink(),
        "link_target": os.readlink(path) if path.is_symlink() else None,
        "resolved_path": str(path.resolve()),
    }


def _verify_snapshot(root: Path, *, trusted_uid: int, trusted_gid: int) -> tuple[str, dict[str, Any]]:
    if root.is_symlink() or not root.is_dir():
        raise ReviewRunnerError("governed review snapshot is unavailable")
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            raise ReviewRunnerError("governed review snapshot cannot contain symlinks")
        value = path.stat(follow_symlinks=False)
        if value.st_uid != trusted_uid or value.st_gid != trusted_gid:
            raise ReviewRunnerError("governed review snapshot ownership is not trusted")
        if stat.S_IMODE(value.st_mode) & 0o022:
            raise ReviewRunnerError("governed review snapshot is writable")
        if path.is_file() and value.st_nlink != 1:
            raise ReviewRunnerError("governed review snapshot cannot contain hard links")
    return snapshot_hash(root), _identity(root)


def validate_execution(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema", "provider", "receiver_identity", "card_hash", "handoff_hash",
        "promptcraft_receipt_hash", "source", "source_protection", "plan_commit", "bindings",
        "provider_identity", "invocation", "lifecycle", "output", "review",
        "execution_hash",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != EXECUTION_SCHEMA:
        raise ReviewRunnerError("governed review execution contract is invalid")
    unsigned = dict(value)
    claimed = unsigned.pop("execution_hash")
    if claimed != _hash(unsigned):
        raise ReviewRunnerError("governed review execution hash mismatch")
    if not isinstance(value.get("provider"), str) or not value["provider"]:
        raise ReviewRunnerError("governed review provider identity is missing")
    if value.get("receiver_identity") != f"{value['provider']}:tgw-review":
        raise ReviewRunnerError("governed review receiver identity mismatch")
    provider_identity = value.get("provider_identity")
    identity_fields = {
        "schema", "provider", "authenticated", "account_identity", "version", "skill",
        "configured_command_path", "configured_command_identity",
        "resolved_executable_path", "executable_sha256", "skill_path", "skill_sha256",
        "mcp_config_path", "mcp_config_sha256", "trusted_uid", "trusted_gid",
        "environment_sha256",
    }
    if not isinstance(provider_identity, Mapping) or set(provider_identity) != identity_fields or provider_identity.get("schema") != IDENTITY_SCHEMA:
        raise ReviewRunnerError("governed review live provider identity is invalid")
    if (
        provider_identity.get("provider") != value["provider"]
        or provider_identity.get("authenticated") is not True
        or provider_identity.get("skill") != "tgw-review"
    ):
        raise ReviewRunnerError("governed review provider account/skill is unavailable")
    for field in ("executable_sha256", "skill_sha256", "mcp_config_sha256"):
        if not isinstance(provider_identity.get(field), str) or not provider_identity[field].startswith(_SHA256_PREFIX):
            raise ReviewRunnerError("governed review provider identity hashes are invalid")
    if not isinstance(provider_identity.get("account_identity"), str) or not provider_identity["account_identity"].startswith(_SHA256_PREFIX):
        raise ReviewRunnerError("governed review account identity is invalid")
    source = value.get("source")
    if not isinstance(source, Mapping) or set(source) != {"commit", "tree", "snapshot_hash"}:
        raise ReviewRunnerError("governed review source identity is invalid")
    if not all(isinstance(source.get(field), str) and source[field] for field in source):
        raise ReviewRunnerError("governed review source identity is invalid")
    source_protection = value.get("source_protection")
    if (
        not isinstance(source_protection, Mapping)
        or set(source_protection) != {"trusted_uid", "trusted_gid", "root_identity", "held_through_use"}
        or source_protection.get("held_through_use") is not True
        or not isinstance(source_protection.get("trusted_uid"), int)
        or not isinstance(source_protection.get("trusted_gid"), int)
        or not isinstance(source_protection.get("root_identity"), Mapping)
    ):
        raise ReviewRunnerError("governed review source protection evidence is invalid")
    lifecycle = value.get("lifecycle")
    if not isinstance(lifecycle, Mapping) or lifecycle.get("exit_code") != 0 or lifecycle.get("timed_out") is not False:
        raise ReviewRunnerError("governed review process did not complete cleanly")
    review = value.get("review")
    if not isinstance(review, Mapping) or review.get("schema") != "tgw-code-review/v1":
        raise ReviewRunnerError("governed review semantic result is invalid")
    return dict(value)


def _bounded_run(
    argv: Sequence[str], *, environment: Mapping[str, str], timeout_seconds: float,
    output_limit: int, pass_fds: Sequence[int],
) -> tuple[int, bytes, bytes, bool, bool]:
    process = subprocess.Popen(
        list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dict(environment), start_new_session=True, pass_fds=tuple(pass_fds),
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    for stream in (process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    overflow = False
    while selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        for key, _ in selector.select(min(remaining, 0.1)):
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            buffers[key.fileobj].extend(chunk)
            if sum(len(item) for item in buffers.values()) > output_limit:
                overflow = True
                break
        if overflow:
            break
    if not timed_out and not overflow:
        try:
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
    if timed_out or overflow:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    exit_code = process.wait()
    # A provider can exit while leaving descendants behind.  The process group
    # is always terminated and checked empty before evidence claims reaping.
    try:
        os.killpg(process.pid, signal.SIGTERM)
        time.sleep(0.05)
        os.killpg(process.pid, signal.SIGKILL)
        time.sleep(0.05)
    except ProcessLookupError:
        pass
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise ReviewRunnerError("governed review process group is not empty")
    selector.close()
    return exit_code, bytes(buffers[process.stdout]), bytes(buffers[process.stderr]), timed_out, overflow


def run_governed_review(
    handoff: Mapping[str, Any], *, snapshot: Path, source_commit: str, source_tree: str,
    plan_commit: str, provider: str, provider_identity: Mapping[str, Any],
    provider_argv: Sequence[str],
    environment: Mapping[str, str], trusted_uid: int, trusted_gid: int,
    publish_execution: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    timeout_seconds: float = 900, output_limit: int = 8 * 1024 * 1024,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute and capture one exact provider-neutral tgw-review invocation.

    ``provider_argv`` is the operator-admitted harness command.  It must contain
    exactly one ``{prompt}`` and one ``{snapshot}`` token, preventing the
    adapter from silently changing the provider-specific CLI contract.
    """

    from promptcraft.handoff import HandoffError, verify_for_launcher

    try:
        invocation = verify_for_launcher(handoff, now=now or datetime.now(timezone.utc))
    except HandoffError as exc:
        raise ReviewRunnerError(f"invalid governed review handoff: {exc}") from exc
    card = handoff["card"]
    receiver_identity = f"{provider}:tgw-review"
    if invocation["receiver_identity"] != receiver_identity or card["selected_provider"] != provider:
        raise ReviewRunnerError("Promptcraft handoff does not select the governed review provider")
    if card["plan_commit"] != plan_commit:
        raise ReviewRunnerError("governed review Plan binding is stale")
    expected_snapshot = card["bindings"]["source_tree"]["hash"]
    before_hash, before_identity = _verify_snapshot(snapshot, trusted_uid=trusted_uid, trusted_gid=trusted_gid)
    if before_hash != expected_snapshot:
        raise ReviewRunnerError("governed review source X does not match the card")
    held = validate_execution_identity(provider_identity, provider, provider_argv, environment)
    prompt = handoff["instruction"] + "\nReturn only one JSON object satisfying tgw-code-review/v1.\n"
    if list(provider_argv).count("{prompt}") != 1 or list(provider_argv).count("{snapshot}") != 1:
        raise ReviewRunnerError("governed review provider command framing is invalid")
    if len(prompt.encode()) > 1024 * 1024:
        raise ReviewRunnerError("governed review prompt exceeds its framing limit")
    snapshot_fd = os.open(snapshot, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    executable_fd, skill_fd, mcp_fd = held
    held_before = {
        descriptor: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        for descriptor in (snapshot_fd, executable_fd, skill_fd, mcp_fd)
        for value in (os.fstat(descriptor),)
    }
    argv = [
        prompt if item == "{prompt}" else f"/proc/self/fd/{snapshot_fd}" if item == "{snapshot}" else item
        for item in provider_argv
    ]
    argv[0] = f"/proc/self/fd/{executable_fd}"
    started = datetime.now(timezone.utc)
    try:
        exit_code, stdout, stderr, timed_out, overflow = _bounded_run(
            argv, environment=environment, timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            pass_fds=(snapshot_fd, executable_fd, skill_fd, mcp_fd),
        )
        for descriptor, expected in held_before.items():
            value = os.fstat(descriptor)
            if (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns) != expected:
                raise ReviewRunnerError("governed review held input changed during execution")
        for field, hash_field in (
            ("resolved_executable_path", "executable_sha256"),
            ("skill_path", "skill_sha256"),
            ("mcp_config_path", "mcp_config_sha256"),
        ):
            if _file_hash(Path(str(provider_identity[field]))) != provider_identity[hash_field]:
                raise ReviewRunnerError("governed review named provider input changed")
        if _command_identity(Path(str(provider_identity["configured_command_path"]))) != provider_identity["configured_command_identity"]:
            raise ReviewRunnerError("governed review configured command changed")
    finally:
        for descriptor in (snapshot_fd, executable_fd, skill_fd, mcp_fd):
            os.close(descriptor)
    completed = datetime.now(timezone.utc)
    after_hash, after_identity = _verify_snapshot(snapshot, trusted_uid=trusted_uid, trusted_gid=trusted_gid)
    if (after_hash, after_identity) != (before_hash, before_identity):
        raise ReviewRunnerError("governed review mutated or exchanged source X")
    if timed_out:
        raise ReviewRunnerError("governed review timed out")
    if overflow:
        raise ReviewRunnerError("governed review exceeded its output limit")
    if exit_code != 0:
        raise ReviewRunnerError("governed review failed")
    try:
        envelope = json.loads(stdout)
        if not isinstance(envelope, Mapping) or envelope.get("is_error") is not False:
            raise ValueError
        raw_result = envelope.get("result")
        review = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReviewRunnerError("governed review returned malformed output") from exc
    review = _validate_report(review, expected_snapshot, snapshot)
    unsigned = {
        "schema": EXECUTION_SCHEMA,
        "provider": provider,
        "receiver_identity": receiver_identity,
        "card_hash": card["card_hash"],
        "handoff_hash": handoff["handoff_hash"],
        "promptcraft_receipt_hash": handoff["receipt"]["receipt_hash"],
        "source": {"commit": source_commit, "tree": source_tree, "snapshot_hash": expected_snapshot},
        "source_protection": {
            "trusted_uid": trusted_uid, "trusted_gid": trusted_gid,
            "root_identity": before_identity, "held_through_use": True,
        },
        "plan_commit": plan_commit,
        "bindings": dict(card["bindings"]),
        "provider_identity": dict(provider_identity),
        "invocation": {
            "argv_sha256": _hash(argv), "tool_policy": ["Read", "Bash", "Glob", "Grep"],
            "timeout_seconds": timeout_seconds, "output_limit": output_limit,
        },
        "lifecycle": {
            "started_at": started.isoformat(), "completed_at": completed.isoformat(),
            "exit_code": exit_code, "timed_out": False, "process_group_reaped": True,
        },
        "output": {
            "stdout_sha256": _SHA256_PREFIX + hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": _SHA256_PREFIX + hashlib.sha256(stderr).hexdigest(),
            "stdout_size": len(stdout), "stderr_size": len(stderr),
        },
        "review": review,
    }
    result = {**unsigned, "execution_hash": _hash(unsigned)}
    normalized = validate_execution(result)
    publication = publish_execution(normalized)
    expected_publication = {
        "schema": "tgw-governed-review-publication/v1",
        "sink_ref": card["bindings"]["receipt_sink"]["ref"],
        "execution_hash": normalized["execution_hash"],
    }
    if (
        not isinstance(publication, Mapping)
        or any(publication.get(field) != expected for field, expected in expected_publication.items())
        or not isinstance(publication.get("artifact_ref"), str)
        or not publication["artifact_ref"]
        or publication.get("artifact_hash") != _hash(normalized)
    ):
        raise ReviewRunnerError("bound receipt sink did not retain governed review execution")
    return normalized


def validate_execution_identity(
    identity: Mapping[str, Any], provider: str, provider_argv: Sequence[str],
    environment: Mapping[str, str],
) -> tuple[int, int, int]:
    required = {
        "schema", "provider", "authenticated", "account_identity", "version",
        "skill", "configured_command_path", "configured_command_identity",
        "resolved_executable_path", "executable_sha256", "skill_path",
        "skill_sha256", "mcp_config_path", "mcp_config_sha256", "trusted_uid", "trusted_gid",
        "environment_sha256",
    }
    if not isinstance(identity, Mapping) or set(identity) != required or identity.get("schema") != IDENTITY_SCHEMA:
        raise ReviewRunnerError("governed review provider identity is invalid")
    if (
        identity.get("provider") != provider
        or identity.get("authenticated") is not True
        or identity.get("skill") != "tgw-review"
    ):
        raise ReviewRunnerError("governed review provider account or skill is unavailable")
    if not provider_argv:
        raise ReviewRunnerError("governed review provider executable is unavailable")
    if identity.get("environment_sha256") != _hash(dict(environment)):
        raise ReviewRunnerError("governed review environment identity mismatch")
    allowed_environment = {"HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "SSL_CERT_FILE"}
    if not set(environment) <= allowed_environment or any(
        word in key.upper() for key in environment for word in ("TOKEN", "SECRET", "PASSWORD", "KEY")
    ):
        raise ReviewRunnerError("governed review environment is not closed")
    executable = Path(provider_argv[0])
    configured = Path(str(identity["configured_command_path"]))
    command_identity = identity["configured_command_identity"]
    command_fields = {
        "device", "inode", "uid", "gid", "mode", "nlink", "size", "mtime_ns",
        "is_symlink", "link_target", "resolved_path",
    }
    expected_command_identity = _command_identity(configured)
    if (
        not isinstance(command_identity, Mapping)
        or set(command_identity) != command_fields
        or dict(command_identity) != expected_command_identity
    ):
        raise ReviewRunnerError("governed review configured command identity mismatch")
    if (
        str(executable) != identity.get("resolved_executable_path")
        or executable != configured.resolve()
        or executable.is_symlink()
        or not executable.is_file()
        or _file_hash(executable) != identity.get("executable_sha256")
    ):
        raise ReviewRunnerError("governed review provider executable identity mismatch")
    if not isinstance(identity["trusted_uid"], int) or not isinstance(identity["trusted_gid"], int):
        raise ReviewRunnerError("governed review trusted ownership is invalid")
    paths = (executable, Path(str(identity["skill_path"])), Path(str(identity["mcp_config_path"])))
    hashes = (identity["executable_sha256"], identity["skill_sha256"], identity["mcp_config_sha256"])
    descriptors: list[int] = []
    try:
        for path, expected in zip(paths, hashes, strict=True):
            value = path.stat(follow_symlinks=False)
            if (
                path.is_symlink()
                or not path.is_file()
                or value.st_uid != identity["trusted_uid"]
                or value.st_gid != identity["trusted_gid"]
                or stat.S_IMODE(value.st_mode) & 0o022
                or value.st_nlink != 1
                or _file_hash(path) != expected
            ):
                raise ReviewRunnerError("governed review skill/MCP identity is invalid")
            descriptors.append(os.open(path, os.O_RDONLY | os.O_NOFOLLOW))
        return descriptors[0], descriptors[1], descriptors[2]
    except Exception:
        for descriptor in descriptors:
            os.close(descriptor)
        raise
