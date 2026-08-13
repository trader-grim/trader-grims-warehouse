"""Stdlib-only, no-argument remote helper for host-state observation.

This file is streamed as exact held bytes and executed with the remote system
Python.  It deliberately imports no TGW package and performs no repository,
Nix-store, profile, or service mutation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

REQUEST_SCHEMA = "tgw-prod-a3-host-state-observation-request/v1"
RECEIPT_SCHEMA = "tgw-prod-a3-host-state-observation-receipt/v1"
TERMINAL_SCHEMA = "tgw-prod-a3-host-state-observation-terminal/v1"
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_STORE = re.compile(r"^/nix/store/[0-9a-z]{32}-[A-Za-z0-9+._?=-]+$")


class HelperError(RuntimeError):
    pass


class HelperHold(HelperError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _hash(value: object) -> str:
    return _digest(_canonical(value))


def _terminal(
    *,
    outcome: str,
    stage: str,
    code: str,
    request_sha256: str,
    now: datetime,
    diagnostic: bytes,
) -> dict[str, Any]:
    value = {
        "schema": TERMINAL_SCHEMA,
        "outcome": outcome,
        "stage": stage,
        "code": code,
        "dispatched": True,
        "request_sha256": request_sha256,
        "observed_at": now.isoformat(),
        "diagnostic_b64": base64.b64encode(diagnostic).decode("ascii"),
        "diagnostic_sha256": _digest(diagnostic),
        "diagnostic_size": len(diagnostic),
    }
    value["terminal_sha256"] = _hash(value)
    return value


def _exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise HelperError(f"{label} fields are not exact")
    return value


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise HelperError("freshness timestamp has no timezone")
    return parsed


def _validate_request(value: Any, now: datetime) -> dict[str, Any]:
    request = dict(
        _exact(
            value,
            {
                "schema",
                "operation_id",
                "plan",
                "target",
                "transport",
                "prerequisites",
                "bounds",
                "freshness",
                "policy",
                "request_sha256",
            },
            "request",
        )
    )
    if request["schema"] != REQUEST_SCHEMA:
        raise HelperError("request schema differs")
    target = {
        "host": "tgw-prod",
        "user": "codex",
        "port": 22,
        "system": "x86_64-linux",
        "remote_python": "/usr/bin/python3",
        "remote_git": "/usr/bin/git",
        "repository": "/home/db/tgw-flake",
        "expected_branch": "main",
    }
    if request["target"] != target:
        raise HelperError("request target differs")
    if request["policy"] != {
        "read_only": True,
        "remote_write": False,
        "repository_write": False,
        "nix": False,
        "network_beyond_ssh": False,
        "platform_bootstrap_grant_consumption": False,
    }:
        raise HelperError("request effect policy differs")
    bounds = _exact(request["bounds"], {"timeout_seconds", "max_output_bytes", "max_diagnostic_bytes"}, "bounds")
    for bound in bounds.values():
        if isinstance(bound, bool) or not isinstance(bound, int) or bound <= 0:
            raise HelperError("request bound is invalid")
    if bounds["timeout_seconds"] > 120 or bounds["max_output_bytes"] > 1_048_576 or bounds["max_diagnostic_bytes"] > 262_144:
        raise HelperError("request bound exceeds policy")
    freshness = _exact(request["freshness"], {"issued_at", "expires_at"}, "freshness")
    issued = _parse_time(freshness["issued_at"])
    expires = _parse_time(freshness["expires_at"])
    if expires <= issued or expires - issued > timedelta(minutes=10) or not issued <= now < expires:
        raise HelperError("request is stale")
    claimed = request.pop("request_sha256")
    if not isinstance(claimed, str) or not _SHA.fullmatch(claimed) or claimed != _hash(request):
        raise HelperError("request hash differs")
    request["request_sha256"] = claimed
    return request


def _inode(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_uid,
        st.st_gid,
        stat.S_IMODE(st.st_mode),
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


def _read_fd(fd: int) -> bytes:
    raw = bytearray()
    while True:
        block = os.read(fd, 1 << 20)
        if not block:
            break
        raw.extend(block)
    os.lseek(fd, 0, os.SEEK_SET)
    return bytes(raw)


def _held_executable(path: Path) -> tuple[int, bytes, dict[str, Any]]:
    resolved = path.resolve(strict=True)
    for ancestor in (resolved.parent, *resolved.parents):
        item = os.lstat(ancestor)
        if not stat.S_ISDIR(item.st_mode) or stat.S_ISLNK(item.st_mode) or item.st_uid != 0 or stat.S_IMODE(item.st_mode) & 0o022:
            raise HelperHold("tool ancestor is mutable")
        if ancestor == Path("/"):
            break
    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or not st.st_mode & 0o111 or st.st_nlink != 1:
            raise HelperHold("tool metadata differs")
        raw = _read_fd(fd)
        return (
            fd,
            raw,
            {
                "path": str(path),
                "realpath": str(resolved),
                "sha256": _digest(raw),
                "size": st.st_size,
                "uid": st.st_uid,
                "gid": st.st_gid,
                "mode": stat.S_IMODE(st.st_mode),
                "nlink": st.st_nlink,
                "dev": st.st_dev,
                "ino": st.st_ino,
            },
        )
    except Exception:
        os.close(fd)
        raise


def _kill_group(pgid: int) -> bool:
    for sent in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sent)
        except ProcessLookupError:
            return True
        for _ in range(100):
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.01)
    return False


def _version(fd: int, timeout: int = 10, limit: int = 65536) -> tuple[str, str, str]:
    process = subprocess.Popen(
        [f"/proc/{os.getpid()}/fd/{fd}", "--version"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        pass_fds=(fd,),
    )
    assert process.stdout and process.stderr
    selector = selectors.DefaultSelector()
    outputs = {"out": bytearray(), "err": bytearray()}
    for stream, label in ((process.stdout, "out"), (process.stderr, "err")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, label)
    deadline = time.monotonic() + timeout
    failure: Exception | None = None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("tool version timed out")
            for key, _mask in selector.select(min(remaining, 0.25)):
                block = os.read(key.fd, 65536)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                outputs[key.data].extend(block)
                if len(outputs[key.data]) > limit:
                    raise HelperError("tool version output exceeded bound")
        process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except Exception as exc:
        failure = exc
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    empty = _kill_group(process.pid)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired as exc:
        raise HelperError("tool version leader was not reaped") from exc
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        empty = True
    else:
        empty = False
    if failure is not None or process.returncode != 0 or not empty:
        raise HelperError("tool version observation failed") from failure
    raw = bytes(outputs["out"] or outputs["err"])
    if not raw:
        raise HelperError("tool version output is empty")
    return (
        raw.decode("utf-8", errors="strict").strip(),
        _digest(raw),
        base64.b64encode(raw).decode("ascii"),
    )


def _link(path: Path) -> tuple[os.stat_result, str, dict[str, int]]:
    before = os.lstat(path)
    if not stat.S_ISLNK(before.st_mode):
        raise HelperHold("system identity is not a symlink")
    target = os.readlink(path)
    if not _STORE.fullmatch(target):
        raise HelperHold("system identity is not a Nix store path")
    if _inode(os.lstat(path)) != _inode(before) or os.readlink(path) != target:
        raise HelperError("system identity changed")
    try:
        target_st = os.stat(target, follow_symlinks=True)
    except FileNotFoundError as exc:
        raise HelperHold("system target is absent") from exc
    if not stat.S_ISDIR(target_st.st_mode) or target_st.st_uid != 0 or stat.S_IMODE(target_st.st_mode) & 0o022:
        raise HelperHold("system target is not an immutable root-owned directory")
    return (
        before,
        target,
        {
            "dev": target_st.st_dev,
            "ino": target_st.st_ino,
            "uid": target_st.st_uid,
            "gid": target_st.st_gid,
            "mode": stat.S_IMODE(target_st.st_mode),
        },
    )


def _repository(path: Path, expected_branch: str) -> tuple[dict[str, Any], tuple[tuple[int, ...], ...]]:
    repo_st = os.lstat(path)
    git_st = os.lstat(path / ".git")
    if not stat.S_ISDIR(repo_st.st_mode) or stat.S_ISLNK(repo_st.st_mode) or not stat.S_ISDIR(git_st.st_mode) or stat.S_ISLNK(git_st.st_mode):
        raise HelperHold("repository or .git is not a direct directory")
    head = path / ".git/HEAD"
    ref = path / ".git/refs/heads" / expected_branch
    try:
        fd = os.open(head, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        raise HelperHold("repository HEAD is absent") from exc
    ref_fd = -1
    try:
        try:
            ref_fd = os.open(ref, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as exc:
            raise HelperHold("repository main ref is absent") from exc
        head_st = os.fstat(fd)
        raw = os.read(fd, 4097)
        if not stat.S_ISREG(head_st.st_mode) or head_st.st_nlink != 1 or len(raw) > 4096:
            raise HelperError("repository HEAD metadata differs")
        if raw.decode("ascii", errors="strict").strip() != "ref: refs/heads/" + expected_branch:
            raise HelperHold("repository branch differs")
        if _inode(os.fstat(fd)) != _inode(head_st) or _inode(os.stat(head, follow_symlinks=False)) != _inode(head_st):
            raise HelperError("repository HEAD changed")
        ref_st = os.fstat(ref_fd)
        ref_raw = os.read(ref_fd, 42)
        if (
            not stat.S_ISREG(ref_st.st_mode)
            or ref_st.st_nlink != 1
            or not re.fullmatch(rb"[0-9a-f]{40}\n", ref_raw)
            or _inode(os.fstat(ref_fd)) != _inode(ref_st)
            or _inode(os.stat(ref, follow_symlinks=False)) != _inode(ref_st)
        ):
            raise HelperError("repository branch ref differs")
        result = {
            "path": str(path),
            "branch": expected_branch,
            "uid": repo_st.st_uid,
            "gid": repo_st.st_gid,
            "mode": stat.S_IMODE(repo_st.st_mode),
            "dev": repo_st.st_dev,
            "ino": repo_st.st_ino,
            "head_sha256": _digest(raw),
            "ref_sha256": _digest(ref_raw),
            "commit": ref_raw.decode("ascii").strip(),
        }
        return result, (_inode(repo_st), _inode(git_st), _inode(head_st), _inode(ref_st))
    finally:
        if ref_fd >= 0:
            os.close(ref_fd)
        os.close(fd)


def observe(request: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    current_st, current, current_identity = _link(Path("/run/current-system"))
    profile_st, profile, profile_identity = _link(Path("/nix/var/nix/profiles/system"))
    if current != profile or current_identity != profile_identity:
        raise HelperHold("current and profile CAS differ")
    python_fd, python_raw, python_identity = _held_executable(Path(request["target"]["remote_python"]))
    git_fd, git_raw, git_identity = _held_executable(Path(request["target"]["remote_git"]))
    try:
        proc = os.stat("/proc/self/exe")
        if (proc.st_dev, proc.st_ino) != (os.fstat(python_fd).st_dev, os.fstat(python_fd).st_ino):
            raise HelperHold("helper interpreter differs from observed Python")
        (
            python_identity["version"],
            python_identity["version_sha256"],
            python_identity["version_b64"],
        ) = _version(python_fd)
        (
            git_identity["version"],
            git_identity["version_sha256"],
            git_identity["version_b64"],
        ) = _version(git_fd)
        repository, repo_before = _repository(Path(request["target"]["repository"]), request["target"]["expected_branch"])
        if _inode(os.lstat("/run/current-system")) != _inode(current_st) or _inode(os.lstat("/nix/var/nix/profiles/system")) != _inode(profile_st):
            raise HelperError("system link changed after observation")
        if (
            _inode(os.lstat(request["target"]["repository"])) != repo_before[0]
            or _inode(os.lstat(Path(request["target"]["repository"]) / ".git")) != repo_before[1]
            or _inode(os.stat(Path(request["target"]["repository"]) / ".git/HEAD", follow_symlinks=False)) != repo_before[2]
            or _inode(
                os.stat(
                    Path(request["target"]["repository"]) / ".git/refs/heads" / request["target"]["expected_branch"],
                    follow_symlinks=False,
                )
            )
            != repo_before[3]
        ):
            raise HelperError("repository identity changed after observation")
        for fd, raw, path in (
            (python_fd, python_raw, Path(request["target"]["remote_python"])),
            (git_fd, git_raw, Path(request["target"]["remote_git"])),
        ):
            if _digest(os.pread(fd, len(raw) + 1, 0)) != _digest(raw):
                raise HelperError("held tool changed")
            named = os.stat(path.resolve(strict=True), follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
                raise HelperError("named tool changed")
        value = {
            "schema": RECEIPT_SCHEMA,
            "request_sha256": request["request_sha256"],
            "observed_at": now.isoformat(),
            "target": request["target"],
            "current_cas": current,
            "profile_cas": profile,
            "system_identity": current_identity,
            "tools": {"python": python_identity, "git": git_identity},
            "repository": repository,
            "effects": {"remote_write": False, "repository_write": False, "nix": False},
        }
        value["receipt_sha256"] = _hash(value)
        return value
    finally:
        os.close(git_fd)
        os.close(python_fd)


def helper_main() -> int:
    if len(sys.argv) != 1:
        return 64
    raw = sys.stdin.buffer.read(1_048_577)
    request: dict[str, Any] | None = None
    now = datetime.now(timezone.utc)
    try:
        if len(raw) > 1_048_576:
            raise HelperError("request exceeds helper input bound")
        request = _validate_request(json.loads(raw), now)
        receipt = observe(request, now)
        encoded = _canonical(receipt)
        if len(encoded) > request["bounds"]["max_output_bytes"]:
            raise HelperError("receipt exceeds helper output bound")
        sys.stdout.buffer.write(len(encoded).to_bytes(8, "big") + encoded)
        sys.stdout.buffer.flush()
        return 0
    except HelperHold as exc:
        if request is None:
            return 75
        encoded = _canonical(
            _terminal(
                outcome="HOLD",
                stage="remote",
                code="HOST_NOT_READY",
                request_sha256=request["request_sha256"],
                now=now,
                diagnostic=type(exc).__name__.encode(),
            )
        )
    except Exception as exc:
        if request is None:
            return 65
        encoded = _canonical(
            _terminal(
                outcome="FAILED",
                stage="remote",
                code="HELPER_FAILED",
                request_sha256=request["request_sha256"],
                now=now,
                diagnostic=type(exc).__name__.encode(),
            )
        )
    if len(encoded) > request["bounds"]["max_output_bytes"]:
        return 65
    sys.stdout.buffer.write(len(encoded).to_bytes(8, "big") + encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(helper_main())
