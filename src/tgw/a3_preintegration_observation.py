"""Zero-effect, fail-closed observation of the external ``tgw-prod`` flake.

The production composition intentionally remains unavailable until a dedicated
SSH identity is admitted.  The helper and validators are nevertheless complete
and testable without touching a production host.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

EFFECT_KIND = "tgw-prod-a3-preintegration-observation"
HANDLER_ID = EFFECT_KIND + "@1"
REQUEST_SCHEMA = "tgw-prod-a3-preintegration-observation-request/v1"
RECEIPT_SCHEMA = "tgw-prod-a3-preintegration-observation-receipt/v1"
TERMINAL_SCHEMA = "tgw-prod-a3-preintegration-observation-terminal/v1"
COMPOSITION_SCHEMA = "tgw-prod-a3-preintegration-observation-composition/v1"
PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"
PLAN_SOLUTION = "sha256:d28650c26c6a3d26d6c943597ccb7abd7c6670b1703d9ce941ac5ed7a2d73a4d"
PLAN_CLOSURE = "sha256:bc0c53b2574fc359c629bd213e078fdd2824e5e1c4a98c0c7a347de869d9e6f8"
SOURCE_COMMIT = "4ddf0d462c0be20475ddedb97a6234fd0cd28fb6"
SOURCE_TREE = "c69c73f8e92d831dd2d3c8d44b550336bf908436"
EVIDENCE_COMMIT = "6d897e4a2aea0ea12942ed3c7d769cf3c338da6e"
SOURCE_ARCHIVE = "sha256:9255ed323c4a175746c24bfc885c42f2af2291797ea0f44ef2fd4f2d203462f4"
SOURCE_CANDIDATE = "candidate:sha256:7cce5103c8c063ad326b343732046f7ba68812aad1750bbbc94bd8a148e89dd3"
SOURCE_CATALOG = "sha256:bbf928611111e23d81092ab1f4f61a6613fe1dac21bfc0784b8a9772d566661e"
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_OPERATION = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SOURCE_DESCRIPTOR_SCHEMA = "tgw-reviewed-observation-source/v1"


class ObservationError(RuntimeError):
    pass


class ObservationHold(ObservationError):
    pass


class EvidencePersistenceAmbiguous(ObservationError):
    def __init__(self, terminal: Mapping[str, Any]):
        super().__init__("validated observation evidence could not be persisted atomically")
        self.terminal = dict(terminal)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ObservationError(f"{label} fields are not exact")
    return value


def validate_source_descriptor(value: Any) -> dict[str, Any]:
    fields = {"schema", "checkpoint", "commit", "tree", "archive_sha256", "candidate_identity", "catalog_sha256", "helper_sha256", "descriptor_sha256"}
    source = dict(_exact(value, fields, "reviewed source descriptor"))
    if source["schema"] != SOURCE_DESCRIPTOR_SCHEMA or source["checkpoint"] != EVIDENCE_COMMIT:
        raise ObservationError("reviewed source checkpoint is invalid")
    if not _GIT.fullmatch(str(source["commit"])) or not _GIT.fullmatch(str(source["tree"])):
        raise ObservationError("reviewed source Git identity is invalid")
    for key in ("archive_sha256", "catalog_sha256", "helper_sha256"):
        if not _SHA.fullmatch(str(source[key])):
            raise ObservationError(f"reviewed source {key} is invalid")
    if not isinstance(source["candidate_identity"], str) or not source["candidate_identity"].startswith("candidate:sha256:"):
        raise ObservationError("reviewed candidate identity is invalid")
    claimed = source.pop("descriptor_sha256")
    if claimed != digest(canonical(source)):
        raise ObservationError("reviewed source descriptor hash is invalid")
    source["descriptor_sha256"] = claimed
    return source


def _fixture_source_descriptor() -> dict[str, Any]:
    """Non-production descriptor used only by local tests; production mounts one."""
    value = {
        "schema": SOURCE_DESCRIPTOR_SCHEMA,
        "checkpoint": EVIDENCE_COMMIT,
        "commit": SOURCE_COMMIT,
        "tree": SOURCE_TREE,
        "archive_sha256": SOURCE_ARCHIVE,
        "candidate_identity": SOURCE_CANDIDATE,
        "catalog_sha256": SOURCE_CATALOG,
        "helper_sha256": "sha256:" + "4" * 64,
    }
    value["descriptor_sha256"] = digest(canonical(value))
    return value


def validate_request(value: Any, *, now: datetime | None = None) -> dict[str, Any]:
    request = dict(_exact(value, {"schema", "operation_id", "plan", "source", "target", "transport", "bounds", "freshness", "repo_expectation", "policy", "request_sha256"}, "request"))
    if request["schema"] != REQUEST_SCHEMA or not isinstance(request["operation_id"], str) or not _OPERATION.fullmatch(request["operation_id"]):
        raise ObservationError("request identity is invalid")
    plan = _exact(request["plan"], {"commit", "solution_sha256", "closure_sha256"}, "Plan")
    if dict(plan) != {"commit": PLAN_COMMIT, "solution_sha256": PLAN_SOLUTION, "closure_sha256": PLAN_CLOSURE}:
        raise ObservationError("Plan binding is not exact")
    validate_source_descriptor(request["source"])
    if request["target"] != {"host": "tgw-prod", "repository": "/home/db/tgw-flake", "branch": "main", "system": "x86_64-linux", "user": "codex", "port": 22}:
        raise ObservationError("target is not exact")
    transport = _exact(request["transport"], {"ssh_sha256", "known_hosts_sha256", "identity_sha256", "helper_sha256", "python_sha256", "git_sha256"}, "transport")
    if any(not isinstance(item, str) or not _SHA.fullmatch(item) for item in transport.values()):
        raise ObservationError("transport identities are invalid")
    if transport["helper_sha256"] != request["source"]["helper_sha256"]:
        raise ObservationError("mounted helper differs from reviewed source descriptor")
    bounds = _exact(request["bounds"], {"timeout_seconds", "max_output_bytes", "max_archive_bytes"}, "bounds")
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in bounds.values()):
        raise ObservationError("bounds are invalid")
    if bounds["timeout_seconds"] > 120 or bounds["max_output_bytes"] > 1_048_576 or bounds["max_archive_bytes"] > 64 * 1024 * 1024:
        raise ObservationError("bounds exceed policy")
    if request["policy"] != {"read_only": True, "nix": False, "network_beyond_ssh": False, "writes": False, "authority_consumption": False}:
        raise ObservationError("zero-effect policy is invalid")
    if request["repo_expectation"] != {"uid": 1001, "gid": 1001, "mode": 0o755, "git_dir": ".git", "lock_file": "flake.lock"}:
        raise ObservationError("repository ownership expectation is invalid")
    freshness = _exact(request["freshness"], {"issued_at", "expires_at"}, "freshness")
    issued = datetime.fromisoformat(str(freshness["issued_at"]).replace("Z", "+00:00"))
    expires = datetime.fromisoformat(str(freshness["expires_at"]).replace("Z", "+00:00"))
    if issued.tzinfo is None or expires.tzinfo is None or expires <= issued or expires - issued > timedelta(minutes=10):
        raise ObservationError("request freshness window is invalid")
    if now is not None and not (issued <= now < expires):
        raise ObservationError("request is not fresh")
    claimed = request.pop("request_sha256")
    if claimed != digest(canonical(request)):
        raise ObservationError("request hash is invalid")
    request["request_sha256"] = claimed
    return request


def make_request(*, operation_id: str, transport: Mapping[str, str], source: Mapping[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    source = dict(source or _fixture_source_descriptor())
    transport = dict(transport)
    transport["helper_sha256"] = source["helper_sha256"]
    value = {
        "schema": REQUEST_SCHEMA,
        "operation_id": operation_id,
        "plan": {"commit": PLAN_COMMIT, "solution_sha256": PLAN_SOLUTION, "closure_sha256": PLAN_CLOSURE},
        "source": source,
        "target": {"host": "tgw-prod", "repository": "/home/db/tgw-flake", "branch": "main", "system": "x86_64-linux", "user": "codex", "port": 22},
        "transport": transport,
        "bounds": {"timeout_seconds": 60, "max_output_bytes": 262144, "max_archive_bytes": 64 * 1024 * 1024},
        "freshness": {"issued_at": now.isoformat(), "expires_at": (now + timedelta(minutes=5)).isoformat()},
        "repo_expectation": {"uid": 1001, "gid": 1001, "mode": 0o755, "git_dir": ".git", "lock_file": "flake.lock"},
        "policy": {"read_only": True, "nix": False, "network_beyond_ssh": False, "writes": False, "authority_consumption": False},
    }
    value["request_sha256"] = digest(canonical(value))
    return validate_request(value)


def _verify_repository_components(repo: Path, request: Mapping[str, Any], *, enforce_owner: bool) -> None:
    expectation = request["repo_expectation"]
    repo_stat = os.lstat(repo)
    if not stat.S_ISDIR(repo_stat.st_mode) or stat.S_ISLNK(repo_stat.st_mode):
        raise ObservationError("repository root is not a held directory")
    if enforce_owner and (repo_stat.st_uid, repo_stat.st_gid, stat.S_IMODE(repo_stat.st_mode)) != (expectation["uid"], expectation["gid"], expectation["mode"]):
        raise ObservationHold("repository ownership or mode differs")
    git_dir = repo / expectation["git_dir"]
    git_stat = os.lstat(git_dir)
    if not stat.S_ISDIR(git_stat.st_mode) or stat.S_ISLNK(git_stat.st_mode):
        raise ObservationError("repository .git is not a directory")
    forbidden_files = (git_dir / "objects/info/alternates", git_dir / "info/grafts")
    if any(path.exists() or path.is_symlink() for path in forbidden_files):
        raise ObservationHold("repository uses alternates or grafts")
    replace = git_dir / "refs/replace"
    if replace.exists() and (not replace.is_dir() or any(replace.iterdir())):
        raise ObservationHold("repository uses replacement objects")


def observe_repository(repository: Path, request: Mapping[str, Any], *, enforce_owner: bool = False, git_path: str | None = None) -> tuple[dict[str, Any], bytes]:
    request = validate_request(request)
    repo = repository.resolve(strict=True)
    if repo != repository or not repo.is_dir():
        raise ObservationError("repository path is not a stable directory")
    _verify_repository_components(repo, request, enforce_owner=enforce_owner)
    git_path = git_path or shutil.which("git")
    if not git_path:
        raise ObservationError("Git executable is unavailable")
    env = {"PATH": "/nonexistent", "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0", "HOME": "/nonexistent"}

    def git(*argv: str, binary: bool = False) -> bytes | str:
        closed = [git_path, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false", "-c", "extensions.objectFormat=sha1", "-c", "protocol.file.allow=never"]
        result = subprocess.run([*closed, *argv], cwd=repo, env=env, capture_output=True, timeout=request["bounds"]["timeout_seconds"], check=False, start_new_session=True)
        if result.returncode != 0:
            raise ObservationError("read-only Git observation failed")
        return result.stdout if binary else result.stdout.decode().strip()

    status = git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ObservationHold("production flake is not clean")
    commit, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    if git("symbolic-ref", "--short", "HEAD") != request["target"]["branch"]:
        raise ObservationHold("production flake branch differs")
    if not _GIT.fullmatch(str(commit)) or not _GIT.fullmatch(str(tree)):
        raise ObservationError("Git identities are invalid")
    lock = repo / "flake.lock"
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > request["bounds"]["max_output_bytes"]:
            raise ObservationError("flake.lock is invalid")
        lock_raw = os.read(fd, st.st_size + 1)
        archive = git("archive", "--format=tar", "--prefix=tgw-flake/", str(commit), binary=True)
        os.lseek(fd, 0, os.SEEK_SET)
        lock_after = os.read(fd, st.st_size + 1)
        st_after = os.fstat(fd)
    finally:
        os.close(fd)
    assert isinstance(archive, bytes)
    if lock_after != lock_raw or (st.st_dev, st.st_ino, st.st_size) != (st_after.st_dev, st_after.st_ino, st_after.st_size):
        raise ObservationHold("flake.lock changed during observation")
    if git("rev-parse", "HEAD") != commit or git("rev-parse", "HEAD^{tree}") != tree or git("status", "--porcelain=v1", "--untracked-files=all"):
        raise ObservationHold("repository changed during observation")
    if len(archive) > request["bounds"]["max_archive_bytes"]:
        raise ObservationError("archive exceeds bound")
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "outcome": "PASS",
        "request_sha256": request["request_sha256"],
        "repository": {"commit": commit, "tree": tree, "clean": True, "archive_sha256": digest(archive), "archive_size": len(archive), "flake_lock_sha256": digest(lock_raw)},
        "effects": {"nix": False, "store": False, "build": False, "write": False, "install": False, "profile": False, "deploy": False, "keygen": False, "authority_consumption": False},
    }
    receipt["receipt_sha256"] = digest(canonical(receipt))
    return validate_receipt(receipt, request), archive


def validate_receipt(value: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    receipt = dict(_exact(value, {"schema", "outcome", "request_sha256", "repository", "effects", "receipt_sha256"}, "receipt"))
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["outcome"] != "PASS" or receipt["request_sha256"] != request["request_sha256"]:
        raise ObservationError("receipt binding is invalid")
    repo = _exact(receipt["repository"], {"commit", "tree", "clean", "archive_sha256", "archive_size", "flake_lock_sha256"}, "repository receipt")
    if not _GIT.fullmatch(str(repo["commit"])) or not _GIT.fullmatch(str(repo["tree"])) or repo["clean"] is not True:
        raise ObservationError("repository receipt is invalid")
    if (
        not _SHA.fullmatch(str(repo["archive_sha256"]))
        or not _SHA.fullmatch(str(repo["flake_lock_sha256"]))
        or isinstance(repo["archive_size"], bool)
        or not isinstance(repo["archive_size"], int)
        or repo["archive_size"] <= 0
    ):
        raise ObservationError("repository hashes are invalid")
    expected_effects = {"nix": False, "store": False, "build": False, "write": False, "install": False, "profile": False, "deploy": False, "keygen": False, "authority_consumption": False}
    if receipt["effects"] != expected_effects:
        raise ObservationError("receipt claims forbidden effects")
    claimed = receipt.pop("receipt_sha256")
    if claimed != digest(canonical(receipt)):
        raise ObservationError("receipt hash is invalid")
    receipt["receipt_sha256"] = claimed
    return receipt


def replay_archive(archive: bytes, receipt: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    """Independently reconstruct the exact tree and lock identity from held bytes."""
    validated = validate_receipt(receipt, request)
    if digest(archive) != validated["repository"]["archive_sha256"] or len(archive) != validated["repository"]["archive_size"]:
        raise ObservationError("archive byte identity differs")
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as stream, tempfile.TemporaryDirectory(prefix="tgw-a3-replay-") as temporary:
        if stream.pax_headers != {"comment": validated["repository"]["commit"]}:
            raise ObservationError("archive PAX commit binding is invalid")
        root = Path(temporary) / "tgw-flake"
        members = stream.getmembers()
        if not members or len(members) > 100_000:
            raise ObservationError("archive member count is invalid")
        seen: set[str] = set()
        for member in members:
            parts = Path(member.name).parts
            if not parts or parts[0] != "tgw-flake" or ".." in parts or ".git" in parts or member.name in seen:
                raise ObservationError("archive path is invalid or duplicated")
            seen.add(member.name)
            if not (member.isdir() or member.isreg()):
                raise ObservationError("archive contains a forbidden member type")
            destination = Path(temporary).joinpath(*parts)
            if member.isdir():
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = stream.extractfile(member)
                if source is None:
                    raise ObservationError("archive regular member is unreadable")
                raw = source.read(request["bounds"]["max_archive_bytes"] + 1)
                if len(raw) != member.size:
                    raise ObservationError("archive member size differs")
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, member.mode & 0o777)
                try:
                    view = memoryview(raw)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise ObservationError("archive replay write failed")
                        view = view[written:]
                finally:
                    os.close(fd)
        env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0"}
        commands = (["git", "init", "-q"], ["git", "-c", "core.hooksPath=/dev/null", "add", "-f", "-A"], ["git", "write-tree"])
        output = ""
        for argv in commands:
            result = subprocess.run(argv, cwd=root, env=env, capture_output=True, timeout=request["bounds"]["timeout_seconds"], check=False)
            if result.returncode:
                raise ObservationError("archive tree replay failed")
            output = result.stdout.decode().strip()
        if output != validated["repository"]["tree"]:
            raise ObservationError("archive replay tree differs")
        lock_raw = (root / "flake.lock").read_bytes()
        if digest(lock_raw) != validated["repository"]["flake_lock_sha256"]:
            raise ObservationError("archive lock differs")
        try:
            lock = json.loads(lock_raw)
        except json.JSONDecodeError as exc:
            raise ObservationError("flake.lock JSON is invalid") from exc
        if not isinstance(lock, dict) or not isinstance(lock.get("nodes"), dict) or not isinstance(lock.get("root"), str):
            raise ObservationError("flake.lock input graph is invalid")
        return {"tree": output, "lock_sha256": digest(lock_raw), "lock_nodes": sorted(lock["nodes"])}


_TERMINALS = {
    ("PASS", "complete", "NONE", True),
    ("HOLD", "predispatch", "PROVIDER_NOT_READY", False),
    ("HOLD", "repository", "REPOSITORY_DIRTY", False),
    ("HOLD", "freshness", "REQUEST_EXPIRED", False),
    ("FAILED", "request", "REQUEST_INVALID", False),
    ("FAILED", "helper", "HELPER_INVALID", True),
    ("FAILED", "replay", "ARCHIVE_REPLAY_FAILED", True),
    ("AMBIGUOUS", "dispatch", "POSTDISPATCH_UNCERTAIN", True),
    ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", True),
}


def terminal(*, outcome: str, stage: str, code: str, dispatched: bool, request_sha256: str, observed_at: str, diagnostic: bytes = b"") -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": TERMINAL_SCHEMA,
        "outcome": outcome,
        "stage": stage,
        "code": code,
        "dispatched": dispatched,
        "request_sha256": request_sha256,
        "observed_at": observed_at,
        "diagnostic": {"bytes": len(diagnostic), "sha256": digest(diagnostic)},
        "effects": {"nix": False, "store": False, "build": False, "write": False, "install": False, "profile": False, "deploy": False, "keygen": False, "authority_consumption": False},
    }
    value["terminal_sha256"] = digest(canonical(value))
    return validate_terminal(value)


def validate_terminal(value: Any) -> dict[str, Any]:
    fields = {"schema", "outcome", "stage", "code", "dispatched", "request_sha256", "observed_at", "diagnostic", "effects", "terminal_sha256"}
    item = dict(_exact(value, fields, "terminal"))
    if item["schema"] != TERMINAL_SCHEMA or (item["outcome"], item["stage"], item["code"], item["dispatched"]) not in _TERMINALS:
        raise ObservationError("terminal state tuple is invalid")
    try:
        observed = datetime.fromisoformat(str(item["observed_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ObservationError("terminal observed_at is invalid") from exc
    if observed.tzinfo is None:
        raise ObservationError("terminal observed_at lacks timezone")
    diagnostic = _exact(item["diagnostic"], {"bytes", "sha256"}, "terminal diagnostic")
    if isinstance(diagnostic["bytes"], bool) or not isinstance(diagnostic["bytes"], int) or not 0 <= diagnostic["bytes"] <= 262144 or not _SHA.fullmatch(str(diagnostic["sha256"])):
        raise ObservationError("terminal diagnostic identity is invalid")
    if diagnostic["bytes"] == 0 and diagnostic["sha256"] != digest(b""):
        raise ObservationError("empty terminal diagnostic hash is invalid")
    expected_effects = {"nix": False, "store": False, "build": False, "write": False, "install": False, "profile": False, "deploy": False, "keygen": False, "authority_consumption": False}
    if item["effects"] != expected_effects:
        raise ObservationError("terminal effects are invalid")
    claimed = item.pop("terminal_sha256")
    if claimed != digest(canonical(item)):
        raise ObservationError("terminal hash is invalid")
    item["terminal_sha256"] = claimed
    return item


@dataclass(frozen=True)
class Composition:
    schema: str = COMPOSITION_SCHEMA
    status: str = "NOT_EXECUTABLE"
    reason: str = "dedicated production SSH authentication identity is not admitted"

    def execute(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        validate_request(request)
        raise ObservationHold(self.reason)


def _held_regular(path: Path, expected_sha256: str, *, executable: bool = False) -> tuple[int, bytes]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (executable and not st.st_mode & 0o111):
            raise ObservationError("held artifact type or mode is invalid")
        raw = b""
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            raw += chunk
        if digest(raw) != expected_sha256:
            raise ObservationError("held artifact digest differs")
        os.lseek(fd, 0, os.SEEK_SET)
        return fd, raw
    except Exception:
        os.close(fd)
        raise


def _sealed(name: str, raw: bytes) -> int:
    import fcntl

    fd = os.memfd_create(name, os.MFD_ALLOW_SEALING)
    os.write(fd, raw)
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
    os.lseek(fd, 0, os.SEEK_SET)
    return fd


@dataclass(frozen=True)
class SshObservationProvider:
    request: Mapping[str, Any]
    ssh_path: Path
    known_hosts_path: Path
    identity_path: Path
    helper_path: Path
    python_path: str

    def ready(self, request: Mapping[str, Any]) -> bool:
        try:
            if validate_request(request)["request_sha256"] != validate_request(self.request)["request_sha256"]:
                return False
            fds = [
                _held_regular(self.ssh_path, request["transport"]["ssh_sha256"], executable=True)[0],
                _held_regular(self.known_hosts_path, request["transport"]["known_hosts_sha256"])[0],
                _held_regular(self.identity_path, request["transport"]["identity_sha256"])[0],
                _held_regular(self.helper_path, request["transport"]["helper_sha256"])[0],
            ]
            for fd in fds:
                os.close(fd)
            return True
        except Exception:
            return False

    def observe(self, request: Mapping[str, Any], *, on_dispatch: Any = lambda: None) -> Mapping[str, Any]:
        request = validate_request(request, now=datetime.now(timezone.utc))
        ssh_fd, _ = _held_regular(self.ssh_path, request["transport"]["ssh_sha256"], executable=True)
        hosts_fd, hosts = _held_regular(self.known_hosts_path, request["transport"]["known_hosts_sha256"])
        identity_fd, identity = _held_regular(self.identity_path, request["transport"]["identity_sha256"])
        helper_fd, helper = _held_regular(self.helper_path, request["transport"]["helper_sha256"])
        sealed_hosts = _sealed("a3-observation-hosts", hosts)
        sealed_identity = _sealed("a3-observation-identity", identity)
        try:
            bootstrap = "ns={'__name__':'tgw_remote_helper'};exec(compile(" + repr(helper.decode()) + ",'a3-helper','exec'),ns);raise SystemExit(ns['helper_main']())"
            remote = shlex.join([self.python_path, "-I", "-c", bootstrap])
            argv = [
                f"/proc/{os.getpid()}/fd/{ssh_fd}",
                "-F",
                "/dev/null",
                "-p",
                str(request["target"]["port"]),
                "-oBatchMode=yes",
                "-oIdentitiesOnly=yes",
                "-oIdentityAgent=none",
                "-oClearAllForwardings=yes",
                "-oStrictHostKeyChecking=yes",
                f"-oUserKnownHostsFile=/proc/{os.getpid()}/fd/{sealed_hosts}",
                f"-oIdentityFile=/proc/{os.getpid()}/fd/{sealed_identity}",
                "-oPasswordAuthentication=no",
                f"{request['target']['user']}@{request['target']['host']}",
                remote,
            ]
            on_dispatch()
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, pass_fds=(ssh_fd, sealed_hosts, sealed_identity))
            try:
                stdout, stderr = process.communicate(canonical(request), timeout=request["bounds"]["timeout_seconds"])
            except subprocess.TimeoutExpired as exc:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                raise ObservationError("SSH observation timed out and process group was terminated") from exc
            if len(stdout) > request["bounds"]["max_archive_bytes"] + request["bounds"]["max_output_bytes"] + 16 or len(stderr) > request["bounds"]["max_output_bytes"]:
                raise ObservationError("SSH observation output exceeded bound")
            if process.returncode != 0:
                raise ObservationError("SSH observation helper failed")
            receipt, archive = decode_helper_response(stdout, request)
            return {"receipt": receipt, "archive": archive}
        finally:
            for fd in (sealed_identity, sealed_hosts, helper_fd, identity_fd, hosts_fd, ssh_fd):
                os.close(fd)


class ImmutableEvidenceStore:
    def __init__(self, root: Path, *, trusted_uid: int | None = None):
        self.root = root
        self.trusted_uid = os.getuid() if trusted_uid is None else trusted_uid

    def persist(self, receipt: Mapping[str, Any], archive: bytes, request: Mapping[str, Any] | None = None) -> tuple[Path, ...]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        root_stat = os.fstat(root_fd)
        if root_stat.st_uid != self.trusted_uid or stat.S_IMODE(root_stat.st_mode) != 0o700:
            os.close(root_fd)
            raise ObservationError("evidence root ownership or mode is invalid")
        identity = str(receipt["receipt_sha256"]).split(":", 1)[1]
        request_raw = canonical(request or {"request_sha256": receipt["request_sha256"]})
        receipt_raw = canonical(receipt)
        manifest = {"request_sha256": digest(request_raw), "receipt_sha256": digest(receipt_raw), "archive_sha256": digest(archive), "archive_size": len(archive)}
        items = ((f"{identity}.request.json", request_raw), (f"{identity}.receipt.json", receipt_raw), (f"{identity}.tar", archive), (f"{identity}.manifest.json", canonical(manifest)))
        paths: list[Path] = []
        try:
            for name, raw in items:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=root_fd)
                try:
                    view = memoryview(raw)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise ObservationError("evidence write was incomplete")
                        view = view[written:]
                    os.fsync(fd)
                    os.lseek(fd, 0, os.SEEK_SET)
                finally:
                    os.close(fd)
                check_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
                try:
                    if digest(os.read(check_fd, len(raw) + 1)) != digest(raw):
                        raise ObservationError("evidence readback differs")
                finally:
                    os.close(check_fd)
                paths.append(self.root / name)
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
        return tuple(paths)


def persist_evidence(
    store: ImmutableEvidenceStore,
    *,
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    archive: bytes,
    observed_at: str,
) -> tuple[Path, ...]:
    """Retain validated in-memory facts when durable state becomes uncertain."""
    request = validate_request(request)
    receipt = validate_receipt(receipt, request)
    replay_archive(archive, receipt, request)
    try:
        return store.persist(receipt, archive, request)
    except Exception as exc:
        ambiguous = terminal(
            outcome="AMBIGUOUS",
            stage="persistence",
            code="PERSISTENCE_UNCERTAIN",
            dispatched=True,
            request_sha256=request["request_sha256"],
            observed_at=observed_at,
            diagnostic=type(exc).__name__.encode(),
        )
        raise EvidencePersistenceAmbiguous(ambiguous) from exc


def encode_helper_response(receipt: Mapping[str, Any], archive: bytes) -> bytes:
    header = canonical(receipt)
    return len(header).to_bytes(8, "big") + len(archive).to_bytes(8, "big") + header + archive


def decode_helper_response(raw: bytes, request: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    if len(raw) < 16:
        raise ObservationError("helper response is truncated")
    header_size = int.from_bytes(raw[:8], "big")
    archive_size = int.from_bytes(raw[8:16], "big")
    bounds = validate_request(request)["bounds"]
    if header_size <= 0 or header_size > bounds["max_output_bytes"] or archive_size <= 0 or archive_size > bounds["max_archive_bytes"]:
        raise ObservationError("helper response bounds are invalid")
    if len(raw) != 16 + header_size + archive_size:
        raise ObservationError("helper response length is invalid")
    try:
        receipt = json.loads(raw[16 : 16 + header_size])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObservationError("helper receipt is malformed") from exc
    archive = raw[16 + header_size :]
    validated = validate_receipt(receipt, request)
    if digest(archive) != validated["repository"]["archive_sha256"] or len(archive) != validated["repository"]["archive_size"]:
        raise ObservationError("helper archive differs from receipt")
    replay_archive(archive, validated, request)
    return validated, archive


def helper_main() -> int:
    """Fixed no-argument remote helper.  It performs only the read-only observation."""
    if len(sys.argv) != 1:
        return 64
    request_raw = sys.stdin.buffer.read(1_048_577)
    try:
        if len(request_raw) > 1_048_576:
            raise ObservationError("request exceeds helper bound")
        request = validate_request(json.loads(request_raw), now=datetime.now(timezone.utc))
        python_real = Path(sys.executable).resolve(strict=True)
        git_real = Path("/run/current-system/sw/bin/git").resolve(strict=True)
        for path, expected in (
            (python_real, request["transport"]["python_sha256"]),
            (git_real, request["transport"]["git_sha256"]),
        ):
            fd, _ = _held_regular(path, expected, executable=True)
            os.close(fd)
        receipt, archive = observe_repository(Path("/home/db/tgw-flake"), request, enforce_owner=True, git_path=str(git_real))
        sys.stdout.buffer.write(encode_helper_response(receipt, archive))
        return 0
    except ObservationHold:
        return 75
    except Exception:
        return 65


def main() -> int:
    """Controller entrypoint; fail closed until an admitted SSH composition exists."""
    if len(sys.argv) != 1:
        return 64
    try:
        request = validate_request(json.load(sys.stdin))
        Composition().execute(request)
    except ObservationHold as exc:
        json.dump({"schema": COMPOSITION_SCHEMA, "status": "HOLD", "reason": str(exc)}, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        return 75
    except Exception:
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
