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
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

EFFECT_KIND = "tgw-prod-a3-preintegration-observation"
HANDLER_ID = EFFECT_KIND + "@1"
REQUEST_SCHEMA = "tgw-prod-a3-preintegration-observation-request/v1"
RECEIPT_SCHEMA = "tgw-prod-a3-preintegration-observation-receipt/v1"
COMPOSITION_SCHEMA = "tgw-prod-a3-preintegration-observation-composition/v1"
PLAN_COMMIT = "fb9fee3e"
SOURCE_COMMIT = "4ddf0d462c0be20475ddedb97a6234fd0cd28fb6"
SOURCE_TREE = "c69c73f8e92d831dd2d3c8d44b550336bf908436"
EVIDENCE_COMMIT = "6d897e4"
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")


class ObservationError(RuntimeError):
    pass


class ObservationHold(ObservationError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ObservationError(f"{label} fields are not exact")
    return value


def validate_request(value: Any) -> dict[str, Any]:
    request = dict(_exact(value, {"schema", "operation_id", "plan", "source", "target", "transport", "bounds", "policy", "request_sha256"}, "request"))
    if request["schema"] != REQUEST_SCHEMA or not isinstance(request["operation_id"], str) or not request["operation_id"]:
        raise ObservationError("request identity is invalid")
    if request["plan"] != {"commit": PLAN_COMMIT}:
        raise ObservationError("Plan binding is not exact")
    if request["source"] != {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE, "evidence_commit": EVIDENCE_COMMIT}:
        raise ObservationError("source binding is not exact")
    if request["target"] != {"host": "tgw-prod", "repository": "/home/db/tgw-flake", "system": "x86_64-linux"}:
        raise ObservationError("target is not exact")
    transport = _exact(request["transport"], {"ssh_sha256", "known_hosts_sha256", "identity_sha256", "helper_sha256"}, "transport")
    if any(not isinstance(item, str) or not _SHA.fullmatch(item) for item in transport.values()):
        raise ObservationError("transport identities are invalid")
    bounds = _exact(request["bounds"], {"timeout_seconds", "max_output_bytes", "max_archive_bytes"}, "bounds")
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in bounds.values()):
        raise ObservationError("bounds are invalid")
    if bounds["timeout_seconds"] > 120 or bounds["max_output_bytes"] > 1_048_576 or bounds["max_archive_bytes"] > 64 * 1024 * 1024:
        raise ObservationError("bounds exceed policy")
    if request["policy"] != {"read_only": True, "nix": False, "network_beyond_ssh": False, "writes": False, "authority_consumption": False}:
        raise ObservationError("zero-effect policy is invalid")
    claimed = request.pop("request_sha256")
    if claimed != digest(canonical(request)):
        raise ObservationError("request hash is invalid")
    request["request_sha256"] = claimed
    return request


def make_request(*, operation_id: str, transport: Mapping[str, str]) -> dict[str, Any]:
    value = {
        "schema": REQUEST_SCHEMA, "operation_id": operation_id, "plan": {"commit": PLAN_COMMIT},
        "source": {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE, "evidence_commit": EVIDENCE_COMMIT},
        "target": {"host": "tgw-prod", "repository": "/home/db/tgw-flake", "system": "x86_64-linux"},
        "transport": dict(transport),
        "bounds": {"timeout_seconds": 60, "max_output_bytes": 262144, "max_archive_bytes": 64 * 1024 * 1024},
        "policy": {"read_only": True, "nix": False, "network_beyond_ssh": False, "writes": False, "authority_consumption": False},
    }
    value["request_sha256"] = digest(canonical(value))
    return validate_request(value)


def observe_repository(repository: Path, request: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    request = validate_request(request)
    repo = repository.resolve(strict=True)
    if repo != repository or not repo.is_dir():
        raise ObservationError("repository path is not a stable directory")
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent"}
    def git(*argv: str, binary: bool = False) -> bytes | str:
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *argv], cwd=repo, env=env, capture_output=True, timeout=request["bounds"]["timeout_seconds"], check=False)
        if result.returncode != 0:
            raise ObservationError("read-only Git observation failed")
        return result.stdout if binary else result.stdout.decode().strip()
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ObservationHold("production flake is not clean")
    commit, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    if not _GIT.fullmatch(str(commit)) or not _GIT.fullmatch(str(tree)):
        raise ObservationError("Git identities are invalid")
    lock = repo / "flake.lock"
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > request["bounds"]["max_output_bytes"]:
            raise ObservationError("flake.lock is invalid")
        lock_raw = os.read(fd, st.st_size + 1)
    finally:
        os.close(fd)
    archive = git("archive", "--format=tar", "--prefix=tgw-flake/", str(commit), binary=True)
    assert isinstance(archive, bytes)
    if len(archive) > request["bounds"]["max_archive_bytes"]:
        raise ObservationError("archive exceeds bound")
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA, "outcome": "PASS", "request_sha256": request["request_sha256"],
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


@dataclass(frozen=True)
class Composition:
    schema: str = COMPOSITION_SCHEMA
    status: str = "NOT_EXECUTABLE"
    reason: str = "dedicated production SSH authentication identity is not admitted"

    def execute(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        validate_request(request)
        raise ObservationHold(self.reason)


class ImmutableEvidenceStore:
    def __init__(self, root: Path):
        self.root = root

    def persist(self, receipt: Mapping[str, Any], archive: bytes) -> tuple[Path, Path]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        identity = str(receipt["receipt_sha256"]).split(":", 1)[1]
        paths = (self.root / f"{identity}.json", self.root / f"{identity}.tar")
        for path, raw in zip(paths, (canonical(receipt), archive), strict=True):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            try:
                os.write(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)
        return paths


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
    return validated, archive


def helper_main() -> int:
    """Fixed no-argument remote helper.  It performs only the read-only observation."""
    if len(sys.argv) != 1:
        return 64
    request_raw = sys.stdin.buffer.read(1_048_577)
    try:
        if len(request_raw) > 1_048_576:
            raise ObservationError("request exceeds helper bound")
        request = validate_request(json.loads(request_raw))
        receipt, archive = observe_repository(Path("/home/db/tgw-flake"), request)
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
