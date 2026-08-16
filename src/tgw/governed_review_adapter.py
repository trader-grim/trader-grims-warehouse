"""Root-captured, provider-neutral adapter for the established ``tgw-review`` path.

This is not a model reviewer.  It launches the selected qualified harness with
the canonical provider-neutral review skill/MCP context and retains an
admission-verifiable execution record.  Any qualified harness can use the same
contract. QES is a separate optional execution path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from tgw.review_runner import ReviewRunnerError, _validate_report

EXECUTION_SCHEMA = "tgw-governed-review-execution/v1"
IDENTITY_SCHEMA = "tgw-governed-review-provider-identity/v1"
_SHA256_PREFIX = "sha256:"
_SANDBOX_FLAGS = (
    "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
    "--die-with-parent", "--new-session", "--tmpfs", "/", "--proc", "/proc",
    "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/home",
)
_CONTEXT_BINDING_NAMES = (
    "plan_input", "plan_commit", "plan_graph", "codegraph_snapshot",
    "source_tree", "execution_environment",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return _SHA256_PREFIX + hashlib.sha256(_canonical(value)).hexdigest()


def _validate_skill_provenance(
    provenance: Any, projection_manifest_hash: Any,
) -> None:
    if not isinstance(provenance, Mapping) or set(provenance) != {
        "schema", "source_ref", "source_manifest_hash",
        "projection_manifest_hash", "projection_receipt_hash",
    }:
        raise ReviewRunnerError("governed review skill source provenance is invalid")
    unsigned = dict(provenance)
    receipt_hash = unsigned.pop("projection_receipt_hash")
    if (
        provenance.get("schema") != "tgw-review-skill-projection-receipt/v1"
        or not isinstance(provenance.get("source_ref"), str)
        or not provenance["source_ref"]
        or not isinstance(provenance.get("source_manifest_hash"), str)
        or not provenance["source_manifest_hash"].startswith(_SHA256_PREFIX)
        or provenance.get("projection_manifest_hash") != projection_manifest_hash
        or receipt_hash != _hash(unsigned)
    ):
        raise ReviewRunnerError("governed review skill source provenance is invalid")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return _SHA256_PREFIX + digest.hexdigest()


def _fd_hash(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    return _SHA256_PREFIX + digest.hexdigest()


def _stat_identity(value: os.stat_result) -> dict[str, Any]:
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


def _identity(path: Path) -> dict[str, Any]:
    return _stat_identity(path.stat(follow_symlinks=False))


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


def _check_policy(value: os.stat_result, policy: Mapping[str, Any], *, label: str) -> None:
    if set(policy) != {"uid", "gid", "forbidden_mode"} or not all(
        isinstance(policy.get(field), int) for field in policy
    ):
        raise ReviewRunnerError(f"{label} owner policy is invalid")
    if value.st_uid != policy["uid"] or value.st_gid != policy["gid"]:
        raise ReviewRunnerError(f"{label} resolved owner is not admitted")
    if stat.S_IMODE(value.st_mode) & policy["forbidden_mode"]:
        raise ReviewRunnerError(f"{label} resolved mode is not admitted")


def _walk_held_tree(
    root_fd: int, *, policy: Mapping[str, Any], label: str,
    max_file_bytes: int = 64 * 1024 * 1024,
    max_total_bytes: int = 512 * 1024 * 1024,
    max_entries: int = 100_000,
    max_depth: int = 64,
    retain_contents: bool = True,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    entries: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    total_bytes = 0

    def visit(directory_fd: int, prefix: str, depth: int) -> None:
        nonlocal total_bytes
        if depth > max_depth:
            raise ReviewRunnerError(f"{label} exceeds its depth bound")
        for name in sorted(os.listdir(directory_fd)):
            if len(entries) >= max_entries:
                raise ReviewRunnerError(f"{label} exceeds its entry bound")
            if not name or "/" in name or name in {".", ".."}:
                raise ReviewRunnerError(f"{label} contains an invalid path")
            relative = f"{prefix}/{name}" if prefix else name
            value = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(value.st_mode):
                raise ReviewRunnerError(f"{label} cannot contain symlinks")
            _check_policy(value, policy, label=label)
            if stat.S_ISDIR(value.st_mode):
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                try:
                    entries.append({"path": relative, "kind": "directory", **_stat_identity(value)})
                    visit(child, relative, depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(value.st_mode):
                if value.st_nlink != 1:
                    raise ReviewRunnerError(f"{label} cannot contain hard links")
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                try:
                    digest = hashlib.sha256()
                    data = bytearray() if retain_contents else None
                    file_bytes = 0
                    while True:
                        block = os.read(descriptor, 1024 * 1024)
                        if not block:
                            break
                        file_bytes += len(block)
                        if file_bytes > max_file_bytes:
                            raise ReviewRunnerError(f"{label} file exceeds its bound")
                        digest.update(block)
                        if data is not None:
                            data.extend(block)
                    total_bytes += file_bytes
                    if total_bytes > max_total_bytes:
                        raise ReviewRunnerError(f"{label} exceeds its aggregate byte bound")
                    entries.append({
                        "path": relative, "kind": "file", **_stat_identity(value),
                        "sha256": _SHA256_PREFIX + digest.hexdigest(),
                    })
                    if data is not None:
                        contents[relative] = bytes(data)
                finally:
                    os.close(descriptor)
            else:
                raise ReviewRunnerError(f"{label} contains a special file")

    root_value = os.fstat(root_fd)
    _check_policy(root_value, policy, label=label)
    visit(root_fd, "", 0)
    return {
        "root_identity": _stat_identity(root_value), "entries": entries,
        "aggregate_bytes": total_bytes,
    }, contents


def _held_snapshot(
    root_fd: int, *, trusted_uid: int, trusted_gid: int,
) -> tuple[str, dict[str, Any]]:
    manifest, contents = _walk_held_tree(
        root_fd,
        policy={"uid": trusted_uid, "gid": trusted_gid, "forbidden_mode": 0o022},
        label="governed review snapshot",
    )
    digest = hashlib.sha256()
    for relative in sorted(contents):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(contents[relative])
        digest.update(b"\0")
    return _SHA256_PREFIX + digest.hexdigest(), manifest["root_identity"]


def snapshot_hash(root: Path) -> str:
    """Hash a protected snapshot after anchoring its root descriptor."""

    value = root.stat(follow_symlinks=False)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        result, _ = _held_snapshot(
            descriptor, trusted_uid=value.st_uid, trusted_gid=value.st_gid,
        )
        return result
    finally:
        os.close(descriptor)


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
        "schema", "provider", "account_identity", "version", "skill", "artifacts",
        "skill_source_provenance", "sandbox_layout",
        "environment_sha256", "argv_template", "argv_template_hash",
        "command_policy", "network_policy", "health",
    }
    if not isinstance(provider_identity, Mapping) or set(provider_identity) != identity_fields or provider_identity.get("schema") != IDENTITY_SCHEMA:
        raise ReviewRunnerError("governed review live provider identity is invalid")
    if (
        provider_identity.get("provider") != value["provider"]
        or provider_identity.get("skill") != "tgw-review"
    ):
        raise ReviewRunnerError("governed review provider account/skill is unavailable")
    artifacts = provider_identity.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {
        "sandbox", "runtime", "context_provider", "executable", "skill_contract",
        "mcp_config", "credential",
    }:
        raise ReviewRunnerError("governed review provider artifacts are invalid")
    _validate_skill_provenance(
        provider_identity.get("skill_source_provenance"),
        artifacts["skill_contract"].get("manifest_hash"),
    )
    if not isinstance(provider_identity.get("account_identity"), str) or not provider_identity["account_identity"].startswith(_SHA256_PREFIX):
        raise ReviewRunnerError("governed review account identity is invalid")
    source = value.get("source")
    if not isinstance(source, Mapping) or set(source) != {"commit", "tree", "snapshot_hash"}:
        raise ReviewRunnerError("governed review source identity is invalid")
    if not all(isinstance(source.get(field), str) and source[field] for field in source):
        raise ReviewRunnerError("governed review source identity is invalid")
    bindings = value.get("bindings")
    command_policy = provider_identity.get("command_policy")
    if (
        not isinstance(bindings, Mapping)
        or not isinstance(command_policy, Mapping)
        or command_policy.get("context_bindings")
        != {name: bindings.get(name) for name in _CONTEXT_BINDING_NAMES}
        or source["snapshot_hash"] != bindings.get("source_tree", {}).get("hash")
    ):
        raise ReviewRunnerError("governed review retained context binding is invalid")
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
    if (
        not isinstance(lifecycle, Mapping)
        or set(lifecycle) != {
            "started_at", "completed_at", "exit_code", "timed_out",
            "outer_process_group_reaped", "containment",
        }
        or lifecycle.get("exit_code") != 0
        or lifecycle.get("timed_out") is not False
        or lifecycle.get("outer_process_group_reaped") is not True
        or lifecycle.get("containment") != "bubblewrap-pid-namespace-empty-on-exit"
    ):
        raise ReviewRunnerError("governed review process did not complete cleanly")
    health = provider_identity.get("health")
    try:
        started = datetime.fromisoformat(str(lifecycle["started_at"]).replace("Z", "+00:00"))
        observed = datetime.fromisoformat(str(health["observed_at"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(health["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewRunnerError("governed review provider health time is invalid") from exc
    if not observed <= started < expires:
        raise ReviewRunnerError("governed review provider health was stale at execution")
    invocation = value.get("invocation")
    if (
        not isinstance(invocation, Mapping)
        or set(invocation) != {
            "argv_sha256", "argv_template_hash", "tool_policy", "skill_contract_hash",
            "skill_manifest_hash", "held_mcp_config", "installed_skill_discovery",
            "sandbox_profile_hash", "pid_namespace", "root_read_only",
            "network_policy_hash",
            "timeout_seconds", "output_limit",
        }
        or invocation.get("argv_template_hash") != provider_identity.get("argv_template_hash")
        or invocation.get("tool_policy") != provider_identity.get("command_policy", {}).get("tool_policy")
        or invocation.get("skill_manifest_hash") != artifacts["skill_contract"].get("manifest_hash")
        or invocation.get("held_mcp_config") is not True
        or invocation.get("installed_skill_discovery") != "protected-held-contract-only"
        or invocation.get("sandbox_profile_hash") != _hash(list(_SANDBOX_FLAGS))
        or invocation.get("pid_namespace") is not True
        or invocation.get("root_read_only") is not True
        or invocation.get("network_policy_hash") != provider_identity.get("network_policy", {}).get("policy_hash")
    ):
        raise ReviewRunnerError("governed review invocation evidence is invalid")
    try:
        completed = datetime.fromisoformat(str(lifecycle["completed_at"]).replace("Z", "+00:00"))
        allowed_duration = float(invocation["timeout_seconds"]) + 3.0
    except (TypeError, ValueError) as exc:
        raise ReviewRunnerError("governed review lifecycle duration is invalid") from exc
    if (
        started.tzinfo is None or completed.tzinfo is None
        or completed < started
        or (completed - started).total_seconds() > allowed_duration
    ):
        raise ReviewRunnerError("governed review lifecycle duration is invalid")
    review = value.get("review")
    if not isinstance(review, Mapping) or review.get("schema") != "tgw-code-review/v1":
        raise ReviewRunnerError("governed review semantic result is invalid")
    return dict(value)


def validate_execution_handoff_binding(
    execution: Mapping[str, Any], card: Mapping[str, Any], handoff: Mapping[str, Any],
) -> None:
    """Cross-bind retained execution bytes to the retained Promptcraft chain."""

    normalized = validate_execution(execution)
    if (
        handoff.get("card") != card
        or normalized["card_hash"] != card.get("card_hash")
        or normalized["bindings"] != card.get("bindings")
        or normalized["handoff_hash"] != handoff.get("handoff_hash")
        or normalized["promptcraft_receipt_hash"]
        != handoff.get("receipt", {}).get("receipt_hash")
        or normalized["provider"] != card.get("selected_provider")
        or normalized["plan_commit"] != card.get("plan_commit")
    ):
        raise ReviewRunnerError("governed review execution/handoff binding mismatch")


def _bounded_run(
    argv: Sequence[str], *, environment: Mapping[str, str], timeout_seconds: float,
    output_limit: int, pass_fds: Sequence[int],
) -> tuple[int, bytes, bytes, bool, bool]:
    selector = selectors.DefaultSelector()
    process: subprocess.Popen[bytes] | None = None
    buffers: dict[Any, bytearray] = {}
    timed_out = False
    overflow = False

    def terminate() -> None:
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired as exc:
                raise ReviewRunnerError("governed review process could not be reaped") from exc

    try:
        process = subprocess.Popen(
            list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(environment), start_new_session=True, pass_fds=tuple(pass_fds),
        )
        assert process.stdout is not None and process.stderr is not None
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
            buffers[stream] = bytearray()
        deadline = time.monotonic() + timeout_seconds
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
            terminate()
        else:
            # Bubblewrap is PID 1 of the provider namespace. Its clean exit is
            # the containment proof: the kernel destroys any escaped session.
            terminate()
        if process.poll() is None:
            raise ReviewRunnerError("governed review process remains live")
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise ReviewRunnerError("governed review outer process group is not empty")
        return (
            int(process.returncode), bytes(buffers[process.stdout]), bytes(buffers[process.stderr]),
            timed_out, overflow,
        )
    except Exception:
        terminate()
        raise
    finally:
        selector.close()
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def run_governed_review(
    handoff: Mapping[str, Any], *, snapshot: Path, source_commit: str, source_tree: str,
    plan_commit: str, provider: str, provider_identity: Mapping[str, Any],
    provider_argv: Sequence[str],
    environment: Mapping[str, str], trusted_uid: int, trusted_gid: int,
    publish_execution: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    read_execution: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    timeout_seconds: float = 900, output_limit: int = 8 * 1024 * 1024,
) -> dict[str, Any]:
    """Execute and capture one exact provider-neutral tgw-review invocation.

    ``provider_argv`` is the operator-admitted harness command.  It must contain
    exactly one ``{prompt}`` and one ``{snapshot}`` token, preventing the
    adapter from silently changing the provider-specific CLI contract.
    """

    from promptcraft.handoff import HandoffError, verify_for_launcher

    execution_observed_at = datetime.now(timezone.utc)
    try:
        invocation = verify_for_launcher(handoff, now=execution_observed_at)
    except HandoffError as exc:
        raise ReviewRunnerError(f"invalid governed review handoff: {exc}") from exc
    card = handoff["card"]
    receiver_identity = f"{provider}:tgw-review"
    if invocation["receiver_identity"] != receiver_identity or card["selected_provider"] != provider:
        raise ReviewRunnerError("Promptcraft handoff does not select the governed review provider")
    if card["plan_commit"] != plan_commit:
        raise ReviewRunnerError("governed review Plan binding is stale")
    if (
        list(provider_argv).count("{prompt}") != 1
        or list(provider_argv).count("{snapshot}") != 1
        or list(provider_argv).count("{mcp_config}") != 1
    ):
        raise ReviewRunnerError("governed review provider command framing is invalid")
    expected_snapshot = card["bindings"]["source_tree"]["hash"]
    snapshot_named_before = _command_identity(snapshot)
    snapshot_fd = os.open(snapshot, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before_hash, before_identity = _held_snapshot(
            snapshot_fd, trusted_uid=trusted_uid, trusted_gid=trusted_gid,
        )
        if before_hash != expected_snapshot:
            raise ReviewRunnerError("governed review source X does not match the card")
        held = validate_execution_identity(
            provider_identity, provider, provider_argv, environment,
            observed_at=execution_observed_at,
        )
        expected_context = {
            name: card["bindings"][name] for name in _CONTEXT_BINDING_NAMES
        }
        if held["context_bindings"] != expected_context:
            raise ReviewRunnerError("governed review context bindings are stale")
    except Exception:
        os.close(snapshot_fd)
        raise
    prompt = "\n".join([
        handoff["instruction"],
        "Use only the protected tgw-review skill installed at "
        f"{held['sandbox_layout']['skill_mount']}; its exact held contract follows.",
        f"Held skill contract hash: {held['skill_contract_hash']}",
        held["skill_contract"],
        "Return only one JSON object satisfying tgw-code-review/v1.",
    ]) + "\n"
    if len(prompt.encode()) > 1024 * 1024:
        for descriptor in (
            snapshot_fd, held["sandbox_fd"], held["executable_fd"],
            held["runtime_fd"], held["context_fd"], held["skill_fd"],
            held["mcp_fd"], held["credential_fd"],
        ):
            os.close(descriptor)
        raise ReviewRunnerError("governed review prompt exceeds its framing limit")
    sandbox_fd = held["sandbox_fd"]
    runtime_fd = held["runtime_fd"]
    context_fd = held["context_fd"]
    executable_fd = held["executable_fd"]
    skill_fd = held["skill_fd"]
    mcp_fd = held["mcp_fd"]
    credential_fd = held["credential_fd"]
    held_before = {
        descriptor: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        for descriptor in (
            snapshot_fd, sandbox_fd, runtime_fd, context_fd, executable_fd, skill_fd, mcp_fd,
            credential_fd,
        )
        for value in (os.fstat(descriptor),)
    }
    provider_command = [
        prompt
        if item == "{prompt}"
        else "/tmp/workspace"
        if item == "{snapshot}"
        else f"/proc/self/fd/{mcp_fd}"
        if item == "{mcp_config}"
        else item
        for item in provider_argv
    ]
    provider_command[0] = f"/proc/self/fd/{executable_fd}"
    layout = held["sandbox_layout"]
    home = PurePosixPath(layout["home"])
    mount_parents = {
        parent
        for configured in (layout["skill_mount"], layout["credential_mount"])
        for parent in PurePosixPath(configured).parents
        if parent != home and home in parent.parents
    }
    layout_directories = [
        item
        for path in sorted(mount_parents, key=lambda item: len(item.parts))
        for item in ("--dir", str(path))
    ]
    argv = [
        f"/proc/self/fd/{sandbox_fd}", *_SANDBOX_FLAGS,
        "--dir", "/usr", "--dir", "/lib", "--dir", "/lib64", "--dir", "/etc",
        "--dir", "/etc/ssl", "--dir", "/opt", "--dir", "/opt/tgw-context",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/usr", "/usr",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/lib", "/lib",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/lib64", "/lib64",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/etc/ssl", "/etc/ssl",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/etc/nsswitch.conf", "/etc/nsswitch.conf",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/etc/hosts", "/etc/hosts",
        "--ro-bind", f"/proc/self/fd/{context_fd}", "/opt/tgw-context",
        "--dir", layout["home"], *layout_directories,
        "--dir", layout["workspace"],
        "--ro-bind", f"/proc/self/fd/{skill_fd}", layout["skill_mount"],
        "--ro-bind", f"/proc/self/fd/{credential_fd}", layout["credential_mount"],
        "--ro-bind", f"/proc/self/fd/{snapshot_fd}", layout["workspace"],
        "--setenv", "HOME", layout["home"], "--chdir", layout["workspace"],
        "--", *provider_command,
    ]
    started = datetime.now(timezone.utc)
    try:
        exit_code, stdout, stderr, timed_out, overflow = _bounded_run(
            argv, environment=environment, timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            pass_fds=(
                snapshot_fd, sandbox_fd, runtime_fd, context_fd, executable_fd, skill_fd,
                mcp_fd,
                credential_fd,
            ),
        )
        for descriptor, expected in held_before.items():
            value = os.fstat(descriptor)
            if (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns) != expected:
                raise ReviewRunnerError("governed review held input changed during execution")
        after_hash, after_identity = _held_snapshot(
            snapshot_fd, trusted_uid=trusted_uid, trusted_gid=trusted_gid,
        )
        if (after_hash, after_identity) != (before_hash, before_identity):
            raise ReviewRunnerError("governed review mutated or exchanged source X")
        revalidated = validate_execution_identity(
            provider_identity, provider, provider_argv, environment,
            observed_at=started,
        )
        try:
            if (
                revalidated["skill_contract_hash"] != held["skill_contract_hash"]
                or revalidated["skill_manifest_hash"] != held["skill_manifest_hash"]
            ):
                raise ReviewRunnerError("governed review skill contract changed")
        finally:
            for descriptor in (
                revalidated["sandbox_fd"], revalidated["runtime_fd"],
                revalidated["context_fd"], revalidated["executable_fd"],
                revalidated["skill_fd"], revalidated["mcp_fd"],
                revalidated["credential_fd"],
            ):
                os.close(descriptor)
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
        review = _validate_report(
            review, expected_snapshot, Path(f"/proc/self/fd/{snapshot_fd}"),
        )
    finally:
        for descriptor in (
            snapshot_fd, sandbox_fd, runtime_fd, context_fd, executable_fd, skill_fd, mcp_fd,
            credential_fd,
        ):
            os.close(descriptor)
    completed = datetime.now(timezone.utc)
    if _command_identity(snapshot) != snapshot_named_before:
        raise ReviewRunnerError("governed review named source root changed")
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
            "argv_sha256": _hash(argv),
            "argv_template_hash": provider_identity["argv_template_hash"],
            "tool_policy": provider_identity["command_policy"]["tool_policy"],
            "skill_contract_hash": held["skill_contract_hash"],
            "skill_manifest_hash": held["skill_manifest_hash"],
            "held_mcp_config": True,
            "installed_skill_discovery": "protected-held-contract-only",
            "sandbox_profile_hash": _hash(list(_SANDBOX_FLAGS)),
            "network_policy_hash": provider_identity["network_policy"]["policy_hash"],
            "pid_namespace": True, "root_read_only": True,
            "timeout_seconds": timeout_seconds, "output_limit": output_limit,
        },
        "lifecycle": {
            "started_at": started.isoformat(), "completed_at": completed.isoformat(),
            "exit_code": exit_code, "timed_out": False,
            "outer_process_group_reaped": True,
            "containment": "bubblewrap-pid-namespace-empty-on-exit",
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
    retained = read_execution(publication)
    if retained != normalized or _hash(retained) != publication["artifact_hash"]:
        raise ReviewRunnerError("bound receipt sink readback differs from governed review execution")
    return normalized


def _open_file_artifact(name: str, artifact: Mapping[str, Any]) -> int:
    required = {
        "kind", "configured_path", "configured_identity", "resolved_path",
        "resolved_identity", "content_sha256", "policy",
    }
    if set(artifact) != required or artifact.get("kind") != "file":
        raise ReviewRunnerError(f"governed review {name} artifact is invalid")
    configured = Path(str(artifact["configured_path"]))
    if _command_identity(configured) != artifact["configured_identity"]:
        raise ReviewRunnerError(f"governed review {name} configured identity mismatch")
    resolved = configured.resolve()
    if str(resolved) != artifact["resolved_path"] or resolved.is_symlink():
        raise ReviewRunnerError(f"governed review {name} resolved path mismatch")
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise ReviewRunnerError(f"governed review {name} must be a single-link file")
        _check_policy(value, artifact["policy"], label=f"governed review {name}")
        if _stat_identity(value) != artifact["resolved_identity"]:
            raise ReviewRunnerError(f"governed review {name} resolved identity mismatch")
        if _fd_hash(descriptor) != artifact["content_sha256"]:
            raise ReviewRunnerError(f"governed review {name} content mismatch")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_tree_artifact(
    name: str, artifact: Mapping[str, Any], *, retain_contents: bool = True,
    max_file_bytes: int = 1024 * 1024,
) -> tuple[int, dict[str, bytes]]:
    required = {
        "kind", "configured_path", "configured_identity", "resolved_path",
        "resolved_identity", "manifest", "manifest_hash", "policy",
    }
    if set(artifact) != required or artifact.get("kind") != "tree":
        raise ReviewRunnerError(f"governed review {name} artifact is invalid")
    configured = Path(str(artifact["configured_path"]))
    if _command_identity(configured) != artifact["configured_identity"]:
        raise ReviewRunnerError(f"governed review {name} configured identity mismatch")
    resolved = configured.resolve()
    if str(resolved) != artifact["resolved_path"] or resolved.is_symlink():
        raise ReviewRunnerError(f"governed review {name} resolved path mismatch")
    descriptor = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        manifest, contents = _walk_held_tree(
            descriptor, policy=artifact["policy"], label=f"governed review {name}",
            max_file_bytes=max_file_bytes,
            max_total_bytes=1024 * 1024 if retain_contents else 1024 * 1024 * 1024,
            max_entries=10_000, max_depth=32, retain_contents=retain_contents,
        )
        if manifest["root_identity"] != artifact["resolved_identity"]:
            raise ReviewRunnerError(f"governed review {name} resolved identity mismatch")
        if manifest != artifact["manifest"] or _hash(manifest) != artifact["manifest_hash"]:
            raise ReviewRunnerError(f"governed review {name} manifest mismatch")
        return descriptor, contents
    except Exception:
        os.close(descriptor)
        raise


def _open_secret_artifact(name: str, artifact: Mapping[str, Any]) -> int:
    required = {
        "kind", "configured_path", "configured_identity", "resolved_path",
        "resolved_identity", "policy", "secret_ref",
    }
    if set(artifact) != required or artifact.get("kind") != "secret-file":
        raise ReviewRunnerError(f"governed review {name} artifact is invalid")
    if not isinstance(artifact.get("secret_ref"), str) or not artifact["secret_ref"]:
        raise ReviewRunnerError(f"governed review {name} secret reference is invalid")
    configured = Path(str(artifact["configured_path"]))
    if _command_identity(configured) != artifact["configured_identity"]:
        raise ReviewRunnerError(f"governed review {name} configured identity mismatch")
    resolved = configured.resolve()
    if str(resolved) != artifact["resolved_path"] or resolved.is_symlink():
        raise ReviewRunnerError(f"governed review {name} resolved path mismatch")
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise ReviewRunnerError(f"governed review {name} must be a single-link file")
        _check_policy(value, artifact["policy"], label=f"governed review {name}")
        if _stat_identity(value) != artifact["resolved_identity"]:
            raise ReviewRunnerError(f"governed review {name} resolved identity mismatch")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _render_skill_contract(contents: Mapping[str, bytes]) -> tuple[str, str]:
    if "SKILL.md" not in contents:
        raise ReviewRunnerError("governed review skill contract lacks SKILL.md")
    sections: list[str] = []
    total = 0
    for relative in sorted(contents):
        try:
            text = contents[relative].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReviewRunnerError("governed review skill contract must be UTF-8 text") from exc
        total += len(contents[relative])
        if total > 1024 * 1024:
            raise ReviewRunnerError("governed review skill contract exceeds its bound")
        content_hash = _SHA256_PREFIX + hashlib.sha256(contents[relative]).hexdigest()
        sections.append(f"--- {relative} ({content_hash}) ---\n{text}")
    rendered = "\n".join(sections)
    return rendered, _SHA256_PREFIX + hashlib.sha256(rendered.encode()).hexdigest()


def validate_execution_identity(
    identity: Mapping[str, Any], provider: str, provider_argv: Sequence[str],
    environment: Mapping[str, str], *, observed_at: datetime,
) -> dict[str, Any]:
    required = {
        "schema", "provider", "account_identity", "version", "skill", "artifacts",
        "skill_source_provenance", "sandbox_layout",
        "environment_sha256", "argv_template", "argv_template_hash",
        "command_policy", "network_policy", "health",
    }
    if not isinstance(identity, Mapping) or set(identity) != required or identity.get("schema") != IDENTITY_SCHEMA:
        raise ReviewRunnerError("governed review provider identity is invalid")
    if (
        identity.get("provider") != provider
        or identity.get("skill") != "tgw-review"
    ):
        raise ReviewRunnerError("governed review provider account/skill is unavailable")
    if not provider_argv:
        raise ReviewRunnerError("governed review provider executable is unavailable")
    layout = identity.get("sandbox_layout")
    if not isinstance(layout, Mapping) or set(layout) != {
        "home", "skill_mount", "credential_mount", "workspace", "context_root",
    }:
        raise ReviewRunnerError("governed review provider sandbox layout is invalid")
    try:
        home = PurePosixPath(layout["home"])
        skill_mount = PurePosixPath(layout["skill_mount"])
        credential_mount = PurePosixPath(layout["credential_mount"])
    except TypeError as exc:
        raise ReviewRunnerError("governed review provider sandbox layout is invalid") from exc
    if (
        str(home) != "/home/reviewer"
        or layout.get("workspace") != "/tmp/workspace"
        or layout.get("context_root") != "/opt/tgw-context"
        or home not in skill_mount.parents
        or home not in credential_mount.parents
        or skill_mount.name != "tgw-review"
        or credential_mount == skill_mount
        or any(part in {"", ".", ".."} for part in (*skill_mount.parts, *credential_mount.parts))
        or environment.get("HOME") != str(home)
    ):
        raise ReviewRunnerError("governed review provider sandbox layout is invalid")
    if list(provider_argv) != identity.get("argv_template") or identity.get("argv_template_hash") != _hash(list(provider_argv)):
        raise ReviewRunnerError("governed review provider argv template mismatch")
    policy = identity.get("command_policy")
    if (
        not isinstance(policy, Mapping)
        or set(policy) != {
            "tool_policy", "read_only", "settings_sources_disabled",
            "held_mcp_config", "held_skill_contract", "sandbox_profile_hash",
            "pid_namespace", "root_read_only", "mcp_commands", "context_bindings",
            "argv_policy_fragments", "forbidden_argv_tokens",
        }
        or policy.get("read_only") is not True
        or policy.get("settings_sources_disabled") is not True
        or policy.get("held_mcp_config") is not True
        or policy.get("held_skill_contract") is not True
        or policy.get("pid_namespace") is not True
        or policy.get("root_read_only") is not True
        or policy.get("sandbox_profile_hash") != _hash(list(_SANDBOX_FLAGS))
        or not isinstance(policy.get("tool_policy"), list)
        or not all(isinstance(item, str) and item for item in policy["tool_policy"])
        or not isinstance(policy.get("mcp_commands"), list)
        or not all(
            isinstance(item, str) and item.startswith("/opt/tgw-context/")
            for item in policy["mcp_commands"]
        )
        or not isinstance(policy.get("context_bindings"), Mapping)
        or set(policy["context_bindings"]) != set(_CONTEXT_BINDING_NAMES)
        or any(
            not isinstance(binding, Mapping)
            or set(binding) != {"ref", "hash"}
            or not isinstance(binding.get("ref"), str)
            or not isinstance(binding.get("hash"), str)
            or not binding["hash"].startswith(_SHA256_PREFIX)
            for binding in policy["context_bindings"].values()
        )
        or not isinstance(policy.get("argv_policy_fragments"), list)
        or not all(
            isinstance(fragment, list)
            and fragment
            and all(isinstance(item, str) for item in fragment)
            for fragment in policy["argv_policy_fragments"]
        )
        or not isinstance(policy.get("forbidden_argv_tokens"), list)
        or not all(
            isinstance(item, str) and item for item in policy["forbidden_argv_tokens"]
        )
    ):
        raise ReviewRunnerError("governed review command policy is invalid")
    forbidden_tools = {"bash", "edit", "write", "notebookedit"}
    if any(item.split("(", 1)[0].lower() in forbidden_tools for item in policy["tool_policy"]):
        raise ReviewRunnerError("governed review command policy permits mutation tools")
    if any(item in provider_argv for item in policy["forbidden_argv_tokens"]):
        raise ReviewRunnerError("governed review command contains a forbidden argument")
    for fragment in policy["argv_policy_fragments"]:
        width = len(fragment)
        if not any(
            list(provider_argv[index:index + width]) == fragment
            for index in range(len(provider_argv) - width + 1)
        ):
            raise ReviewRunnerError("governed review command policy is not enforced by argv")
    if list(provider_argv).count("{mcp_config}") != 1:
        raise ReviewRunnerError("governed review command does not consume held MCP config")
    network_policy = identity.get("network_policy")
    if not isinstance(network_policy, Mapping) or set(network_policy) != {
        "schema", "mode", "endpoints", "policy_hash",
    }:
        raise ReviewRunnerError("governed review network policy is invalid")
    network_unsigned = dict(network_policy)
    network_hash = network_unsigned.pop("policy_hash")
    endpoints = network_policy.get("endpoints")
    endpoint_strings = (
        isinstance(endpoints, list)
        and all(isinstance(item, str) for item in endpoints)
    )
    parsed_endpoints = [urllib_parse.urlsplit(item) for item in endpoints] if endpoint_strings else []
    if (
        network_policy.get("schema") != "tgw-governed-review-network-policy/v1"
        or network_policy.get("mode") != "shared-network-admitted-endpoints"
        or not isinstance(endpoints, list)
        or not endpoints
        or not endpoint_strings
        or endpoints != sorted(set(endpoints))
        or not all(
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            for parsed in parsed_endpoints
        )
        or network_hash != _hash(network_unsigned)
    ):
        raise ReviewRunnerError("governed review network policy is invalid")
    if identity.get("environment_sha256") != _hash(dict(environment)):
        raise ReviewRunnerError("governed review environment identity mismatch")
    allowed_environment = {"HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "SSL_CERT_FILE"}
    if not set(environment) <= allowed_environment or any(
        word in key.upper() for key in environment for word in ("TOKEN", "SECRET", "PASSWORD", "KEY")
    ):
        raise ReviewRunnerError("governed review environment is not closed")
    artifacts = identity.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {
        "sandbox", "runtime", "context_provider", "executable", "skill_contract",
        "mcp_config", "credential",
    }:
        raise ReviewRunnerError("governed review provider artifacts are invalid")
    health = identity.get("health")
    if not isinstance(health, Mapping) or set(health) != {
        "schema", "provider", "account_identity", "observed_at", "expires_at",
        "status", "evidence_hash",
    }:
        raise ReviewRunnerError("governed review provider health evidence is invalid")
    health_unsigned = dict(health)
    claimed_health = health_unsigned.pop("evidence_hash")
    if (
        health.get("schema") != "tgw-governed-review-provider-health/v1"
        or health.get("provider") != provider
        or health.get("account_identity") != identity.get("account_identity")
        or health.get("status") != "AUTHENTICATED"
        or claimed_health != _hash(health_unsigned)
    ):
        raise ReviewRunnerError("governed review provider health evidence is invalid")
    try:
        health_observed = datetime.fromisoformat(str(health["observed_at"]).replace("Z", "+00:00"))
        health_expires = datetime.fromisoformat(str(health["expires_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewRunnerError("governed review provider health time is invalid") from exc
    if not health_observed <= observed_at < health_expires:
        raise ReviewRunnerError("governed review provider health is stale")
    descriptors: list[int] = []
    try:
        sandbox_fd = _open_file_artifact("sandbox", artifacts["sandbox"])
        descriptors.append(sandbox_fd)
        runtime_fd, _ = _open_tree_artifact(
            "runtime", artifacts["runtime"], retain_contents=False,
            max_file_bytes=512 * 1024 * 1024,
        )
        descriptors.append(runtime_fd)
        context_fd, _ = _open_tree_artifact(
            "context provider", artifacts["context_provider"], retain_contents=False,
            max_file_bytes=512 * 1024 * 1024,
        )
        descriptors.append(context_fd)
        context_entries = {
            entry["path"]: entry
            for entry in artifacts["context_provider"]["manifest"]["entries"]
        }
        for command in policy["mcp_commands"]:
            relative = command.removeprefix("/opt/tgw-context/")
            entry = context_entries.get(relative)
            if (
                not relative
                or entry is None
                or entry.get("kind") != "file"
                or not stat.S_IMODE(entry.get("mode", 0)) & 0o111
            ):
                raise ReviewRunnerError(
                    "governed review MCP executable is outside the held context closure"
                )
        executable_fd = _open_file_artifact("executable", artifacts["executable"])
        descriptors.append(executable_fd)
        if Path(provider_argv[0]).resolve() != Path(artifacts["executable"]["resolved_path"]):
            raise ReviewRunnerError("governed review command does not use the retained executable")
        skill_fd, skill_contents = _open_tree_artifact("skill contract", artifacts["skill_contract"])
        descriptors.append(skill_fd)
        _validate_skill_provenance(
            identity.get("skill_source_provenance"),
            artifacts["skill_contract"]["manifest_hash"],
        )
        mcp_fd = _open_file_artifact("MCP config", artifacts["mcp_config"])
        descriptors.append(mcp_fd)
        try:
            mcp = json.loads(os.pread(mcp_fd, 1024 * 1024 + 1, 0))
            servers = mcp["mcpServers"]
            commands = sorted(server["command"] for server in servers.values())
        except (AttributeError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewRunnerError("governed review MCP config is invalid") from exc
        if (
            not isinstance(servers, Mapping)
            or commands != sorted(policy["mcp_commands"])
            or any(
                not isinstance(server, Mapping)
                or set(server) - {"command", "args"}
                or not isinstance(server.get("args", []), list)
                or not all(isinstance(item, str) for item in server.get("args", []))
                for server in servers.values()
            )
        ):
            raise ReviewRunnerError("governed review MCP config is not closure-bound")
        credential_fd = _open_secret_artifact("credential", artifacts["credential"])
        descriptors.append(credential_fd)
        skill_contract, skill_contract_hash = _render_skill_contract(skill_contents)
        return {
            "sandbox_fd": sandbox_fd, "runtime_fd": runtime_fd,
            "context_fd": context_fd,
            "executable_fd": executable_fd,
            "skill_fd": skill_fd, "mcp_fd": mcp_fd, "credential_fd": credential_fd,
            "skill_contract": skill_contract,
            "skill_contract_hash": skill_contract_hash,
            "skill_manifest_hash": artifacts["skill_contract"]["manifest_hash"],
            "context_bindings": dict(policy["context_bindings"]),
            "sandbox_layout": dict(layout),
        }
    except Exception:
        for descriptor in descriptors:
            os.close(descriptor)
        raise


class HTTPReviewEvidenceSink:
    """Bound non-test X publisher with immediate pinned-byte readback."""

    def __init__(self, descriptor: Mapping[str, Any]) -> None:
        if not isinstance(descriptor, Mapping) or set(descriptor) != {
            "schema", "endpoint", "credential_env", "timeout_seconds",
        } or descriptor.get("schema") != "tgw-governed-review-evidence-sink-client/v1":
            raise ReviewRunnerError("governed review evidence sink descriptor is invalid")
        endpoint = descriptor["endpoint"]
        credential_env = descriptor["credential_env"]
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise ReviewRunnerError("governed review evidence sink endpoint must be HTTPS")
        if not isinstance(credential_env, str) or not credential_env:
            raise ReviewRunnerError("governed review evidence sink credential reference is invalid")
        credential = os.environ.get(credential_env)
        if not credential:
            raise ReviewRunnerError("governed review evidence sink credential is unavailable")
        self.endpoint = endpoint.rstrip("/")
        self.timeout = float(descriptor["timeout_seconds"])
        self.authorization = "Bearer " + credential

    def _request(self, request: urllib_request.Request) -> dict[str, Any]:
        request.add_header("Authorization", self.authorization)
        try:
            with urllib_request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(1024 * 1024 + 1)
        except (OSError, urllib_error.URLError) as exc:
            raise ReviewRunnerError("governed review evidence sink request failed") from exc
        if len(body) > 1024 * 1024:
            raise ReviewRunnerError("governed review evidence sink response exceeds its bound")
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewRunnerError("governed review evidence sink response is invalid") from exc
        if not isinstance(value, dict):
            raise ReviewRunnerError("governed review evidence sink response is invalid")
        return value

    def publish(self, execution: Mapping[str, Any]) -> dict[str, Any]:
        body = _canonical(execution)
        return self._request(urllib_request.Request(
            self.endpoint + "/executions", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        ))

    def read(self, publication: Mapping[str, Any]) -> dict[str, Any]:
        artifact_ref = publication.get("artifact_ref")
        if not isinstance(artifact_ref, str) or not artifact_ref:
            raise ReviewRunnerError("governed review publication reference is invalid")
        query = urllib_parse.urlencode({"ref": artifact_ref})
        return self._request(urllib_request.Request(
            self.endpoint + "/artifacts?" + query, method="GET",
        ))


def execute_request(request_path: Path) -> dict[str, Any]:
    """Execute one production request through the provider-neutral path."""

    named_before = _command_identity(request_path)
    descriptor = os.open(request_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        request_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(request_stat.st_mode)
            or request_stat.st_uid != 0
            or stat.S_IMODE(request_stat.st_mode) & 0o022
            or request_stat.st_nlink != 1
        ):
            raise ReviewRunnerError("governed review request is not root-protected")
        body = b""
        while len(body) <= 1024 * 1024:
            block = os.read(descriptor, min(64 * 1024, 1024 * 1024 + 1 - len(body)))
            if not block:
                break
            body += block
        if len(body) > 1024 * 1024:
            raise ReviewRunnerError("governed review request exceeds its bound")
        value = json.loads(body)
        if os.fstat(descriptor) != request_stat:
            raise ReviewRunnerError("governed review request changed while held")
        required = {
            "schema", "handoff", "snapshot", "source_commit", "source_tree",
            "plan_commit", "provider", "provider_identity", "provider_argv",
            "environment", "trusted_uid", "trusted_gid", "timeout_seconds",
            "output_limit", "evidence_sink",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != required
            or value.get("schema") != "tgw-governed-review-request/v1"
        ):
            raise ReviewRunnerError("governed review request contract is invalid")
        sink = HTTPReviewEvidenceSink(value["evidence_sink"])
        result = run_governed_review(
            value["handoff"], snapshot=Path(value["snapshot"]),
            source_commit=value["source_commit"], source_tree=value["source_tree"],
            plan_commit=value["plan_commit"], provider=value["provider"],
            provider_identity=value["provider_identity"],
            provider_argv=value["provider_argv"], environment=value["environment"],
            trusted_uid=value["trusted_uid"], trusted_gid=value["trusted_gid"],
            publish_execution=sink.publish, read_execution=sink.read,
            timeout_seconds=value["timeout_seconds"], output_limit=value["output_limit"],
        )
        if (
            os.fstat(descriptor) != request_stat
            or _command_identity(request_path) != named_before
            or _fd_hash(descriptor)
            != _SHA256_PREFIX + hashlib.sha256(body).hexdigest()
        ):
            raise ReviewRunnerError("governed review request changed during composition")
        return result
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewRunnerError("governed review request is invalid") from exc
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-governed-review")
    parser.add_argument("--request", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        execution = execute_request(arguments.request)
    except (ReviewRunnerError, OSError, ValueError) as exc:
        print(json.dumps({"status": "HOLD", "reason": str(exc)}))
        return 2
    print(json.dumps({
        "status": "PASS", "execution_hash": execution["execution_hash"],
        "provider": execution["provider"], "verdict": execution["review"]["verdict"],
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
