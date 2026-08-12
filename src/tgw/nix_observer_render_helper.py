"""Standalone Phase-A1 source verifier for observer render evaluation.

The production entry point intentionally stops after verifying the immutable
source.  Nix evaluation is an A2 concern and is not reachable from this file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

MAGIC = b"TGWNRH01"
VERSION = 1
PREFIX = struct.Struct("!8sIQQQQ32s32s32s32s")
MAX_HELPER_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 64 * 1024
MAX_TOOL_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
ARCHIVE_ROOT = "trader-grims-warehouse"
GIT_PATH = Path("/run/current-system/sw/bin/git")
SCRATCH_ROOT = Path("/var/tmp/tgw-nix-observer-render-helper")

REQUEST_SCHEMA = "tgw-nix-observer-render-evaluation-request/v1"
TARGET = "nix-input-observer-rendered-artifacts"
PHASE1_FAILURE_SCHEMA = "tgw-nix-observer-render-helper-phase1-failure/v1"
FAILURE_SCHEMA = "tgw-nix-observer-render-helper-failure/v1"
HOLD_SCHEMA = "tgw-nix-observer-render-source-hold/v1"
TEST_MARKER_SCHEMA = "tgw-nix-observer-render-test-marker/v1"
HOLD_OUTCOME = "SOURCE_VERIFIED_NO_EXECUTOR"

OUTPUTS = (
    "etc/nix-input-observer-launcher.conf",
    "etc/nix-input-observer-transport.json",
    "launcher",
    "observer.py",
    "tools/git",
    "tools/ip",
    "tools/nix",
    "tools/nix-store",
    "tools/python",
    "units/tgw-nix-input-observer.slice",
    "units/tgw-nix-input-observer.socket",
    "units/tgw-nix-input-observer@.service",
    "verifier-metadata.json",
)
DIGEST_FIELDS = {
    "archive_sha256",
    "flake_lock_sha256",
    "flake_sha256",
    "module_sha256",
    "launcher_source_sha256",
    "observer_source_sha256",
    "provider_sha256",
    "host_identity_receipt_sha256",
    "systemd_analyze_sha256",
    "systemd_analyze_version_stdout_sha256",
    "input_closure_manifest_sha256",
}
SOURCE_DIGEST_PATHS = {
    "flake_lock_sha256": "flake.lock",
    "flake_sha256": "flake.nix",
    "module_sha256": "nix/nix-input-observer-launcher.nix",
    "launcher_source_sha256": "src/native/tgw_nix_input_observer_launcher.c",
    "observer_source_sha256": "src/tgw/nix_input_observation.py",
    "provider_sha256": "src/tgw/nix_observer_render_evaluation.py",
}
EFFECTS = {
    "build_attempted": False,
    "activation": False,
    "deployment": False,
    "profile_write": False,
    "home_db_write": False,
    "live_flake_write": False,
    "network": False,
}
FAILURE_STAGES = {"request", "tool", "scratch", "archive", "source", "internal", "test-executor", "cleanup"}
FAILURE_CODES = {"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "BOUND_EXCEEDED", "SUBPROCESS_FAILED", "CLEANUP_FAILED", "INTERNAL_ERROR"}

# This is the production bootstrap passed to isolated Python.  Test-only globals
# can replace the fixed Git path or receive a marker, but neither an environment
# variable nor request field is consulted for either capability.
BOOTSTRAP = r'''import hashlib,io,json,struct,sys
P=struct.Struct("!8sIQQQQ32s32s32s32s")
EMPTY=hashlib.sha256(b"").hexdigest()
raw=sys.stdin.buffer.read(P.size)
def canon(v): return json.dumps(v,sort_keys=True,separators=(",",":" )).encode()
def fail(code,b=None):
 b=b or {}
 def hx(name):
  value=b.get(name,b"")
  return "sha256:"+value.hex() if isinstance(value,bytes) and len(value)==32 else "unknown"
 d={"schema":"tgw-nix-observer-render-helper-phase1-failure/v1","outcome":"FAILED","stage":"phase1-bootstrap","diagnostic_code":code}
 d.update({"request_sha256":hx("request"),"helper_sha256":hx("helper"),"tool_sha256":hx("tool"),"archive_sha256":hx("archive")})
 d.update({"request_bytes":b.get("request_bytes",-1),"helper_bytes":b.get("helper_bytes",-1),"tool_bytes":b.get("tool_bytes",-1),"archive_bytes":b.get("archive_bytes",-1)})
 d.update({"cleanup":"not-created","effects":{"build_attempted":False,"activation":False,"deployment":False,"profile_write":False,"home_db_write":False,"live_flake_write":False,"network":False}})
 d["receipt_sha256"]="sha256:"+hashlib.sha256(canon(d)).hexdigest()
 sys.stdout.buffer.write(canon(d)); raise SystemExit(1)
if len(raw)!=P.size: fail("TRUNCATED_PREFIX")
magic,version,hlen,rlen,tlen,alen,rh,hh,th,ah=P.unpack(raw)
b={"request":rh,"helper":hh,"tool":th,"archive":ah,"request_bytes":rlen,"helper_bytes":hlen,"tool_bytes":tlen,"archive_bytes":alen}
if magic!=b"TGWNRH01" or version!=1: fail("BAD_PREFIX",b)
if not (1<=hlen<=1048576 and 1<=rlen<=65536 and 1<=tlen<=67108864 and 1<=alen<=134217728): fail("BAD_LENGTH",b)
helper=sys.stdin.buffer.read(hlen); request_raw=sys.stdin.buffer.read(rlen); archive=sys.stdin.buffer.read(alen); extra=sys.stdin.buffer.read(1)
if len(helper)!=hlen or len(request_raw)!=rlen or len(archive)!=alen or extra: fail("TRUNCATED_OR_TRAILING_BODY",b)
if hashlib.sha256(helper).digest()!=hh: fail("HELPER_HASH_MISMATCH",b)
if hashlib.sha256(archive).digest()!=ah: fail("ARCHIVE_HASH_MISMATCH",b)
try: request=json.loads(request_raw)
except Exception: fail("BAD_REQUEST_JSON",b)
if canon(request)!=request_raw or not isinstance(request,dict): fail("NONCANONICAL_REQUEST",b)
claimed=request.get("request_sha256"); unsigned=dict(request); unsigned.pop("request_sha256",None)
if claimed!="sha256:"+rh.hex() or hashlib.sha256(canon(unsigned)).digest()!=rh: fail("REQUEST_HASH_MISMATCH",b)
if request.get("archive_sha256")!="sha256:"+ah.hex(): fail("ARCHIVE_BINDING_MISMATCH",b)
g={"__name__":"__main__","_BOOTSTRAP_REQUEST_SHA256":"sha256:"+rh.hex(),"_BOOTSTRAP_HELPER_SHA256":"sha256:"+hh.hex(),"_BOOTSTRAP_TOOL_SHA256":"sha256:"+th.hex(),"_BOOTSTRAP_ARCHIVE_SHA256":"sha256:"+ah.hex(),"_BOOTSTRAP_REQUEST_BYTES":rlen,"_BOOTSTRAP_HELPER_BYTES":hlen,"_BOOTSTRAP_TOOL_BYTES":tlen,"_BOOTSTRAP_ARCHIVE_BYTES":alen}
if "_TEST_ONLY_GIT_PATH" in globals(): g["_TEST_ONLY_GIT_PATH"]=globals()["_TEST_ONLY_GIT_PATH"]
sys.stdin=io.TextIOWrapper(io.BytesIO(struct.pack("!Q",rlen)+request_raw+archive))
exec(compile(helper,"<tgw-nix-observer-render-helper>","exec"),g)
'''


class RenderHelperError(ValueError):
    def __init__(self, message: str, *, stage: str, diagnostic_code: str):
        super().__init__(message)
        self.stage = stage
        self.diagnostic_code = diagnostic_code


@dataclass(frozen=True)
class WireBinding:
    request_bytes: int
    helper_bytes: int
    tool_bytes: int
    archive_bytes: int
    request_sha256: str
    helper_sha256: str
    tool_sha256: str
    archive_sha256: str


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest_path(path: Path) -> tuple[str, int]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RenderHelperError("bound path is not regular", stage="tool", diagnostic_code="IDENTITY_MISMATCH")
        digest = hashlib.sha256()
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
        return "sha256:" + digest.hexdigest(), metadata.st_size
    finally:
        os.close(descriptor)


def _validate_request(value: Any) -> dict[str, Any]:
    """Standalone mirror of the frozen render request boundary."""
    fields = {
        "schema",
        "plan_commit",
        "source_commit",
        "source_tree",
        "artifact_ref",
        *DIGEST_FIELDS,
        "target",
        "system",
        "network_policy",
        "allow_ifd",
        "activate",
        "profile_write",
        "home_db_write",
        "expected_outputs",
        "expected_metadata_status",
        "input_closure_manifest",
        "input_closure_path_count",
        "systemd_analyze_version",
        "systemd_analyze_version_stdout_bytes",
        "max_duration_seconds",
        "max_output_bytes",
        "request_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RenderHelperError("render request schema is not closed", stage="request", diagnostic_code="VALIDATION_REFUSED")
    request = dict(value)
    claimed = request.pop("request_sha256")
    if claimed != _sha256(canonical(request)):
        raise RenderHelperError("render request self-hash mismatch", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    if request["schema"] != REQUEST_SCHEMA or request["target"] != TARGET or request["system"] != "x86_64-linux":
        raise RenderHelperError("render target identity mismatch", stage="request", diagnostic_code="VALIDATION_REFUSED")
    if request["network_policy"] != "offline-no-substituters" or request["allow_ifd"] is not False:
        raise RenderHelperError("render purity boundary mismatch", stage="request", diagnostic_code="VALIDATION_REFUSED")
    if any(request[key] is not False for key in ("activate", "profile_write", "home_db_write")):
        raise RenderHelperError("render request contains a forbidden effect", stage="request", diagnostic_code="VALIDATION_REFUSED")
    if request["expected_outputs"] != list(OUTPUTS) or request["expected_metadata_status"] != "NON_DEPLOYABLE_RENDER_FIXTURE":
        raise RenderHelperError("render output contract mismatch", stage="request", diagnostic_code="VALIDATION_REFUSED")
    if not all(isinstance(request[key], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", request[key]) for key in DIGEST_FIELDS):
        raise RenderHelperError("render digest binding invalid", stage="request", diagnostic_code="VALIDATION_REFUSED")
    if not all(isinstance(request[key], str) and re.fullmatch(r"[0-9a-f]{40}", request[key]) for key in ("plan_commit", "source_commit", "source_tree")):
        raise RenderHelperError("render Git identity invalid", stage="request", diagnostic_code="VALIDATION_REFUSED")
    if request["artifact_ref"] != "artifact:" + request["archive_sha256"]:
        raise RenderHelperError("render artifact reference mismatch", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    manifest = request["input_closure_manifest"]
    entry = manifest[0] if isinstance(manifest, list) and len(manifest) == 1 else None
    if (
        request["input_closure_path_count"] != 1
        or not isinstance(entry, dict)
        or set(entry) != {"node", "rev", "lock_nar_hash", "store_path", "nar_sha256"}
        or entry["node"] != "nixpkgs"
        or entry["rev"] != "ac62194c3917d5f474c1a844b6fd6da2db95077d"
        or entry["lock_nar_hash"] != "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w="
        or not isinstance(entry["store_path"], str)
        or not re.fullmatch(r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+", entry["store_path"])
        or not isinstance(entry["nar_sha256"], str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", entry["nar_sha256"])
        or request["input_closure_manifest_sha256"] != _sha256(canonical(manifest))
    ):
        raise RenderHelperError("render input closure binding mismatch", stage="request", diagnostic_code="VALIDATION_REFUSED")
    if request["systemd_analyze_version"] != "systemd 257 (257.10)":
        raise RenderHelperError("render verifier version mismatch", stage="request", diagnostic_code="VALIDATION_REFUSED")
    if type(request["systemd_analyze_version_stdout_bytes"]) is not int or not 1 <= request["systemd_analyze_version_stdout_bytes"] <= 4096:
        raise RenderHelperError("render verifier evidence bound invalid", stage="request", diagnostic_code="BOUND_EXCEEDED")
    if type(request["max_duration_seconds"]) is not int or not 1 <= request["max_duration_seconds"] <= 900:
        raise RenderHelperError("render timeout invalid", stage="request", diagnostic_code="BOUND_EXCEEDED")
    if type(request["max_output_bytes"]) is not int or not 1 <= request["max_output_bytes"] <= 16 * 1024 * 1024:
        raise RenderHelperError("render output bound invalid", stage="request", diagnostic_code="BOUND_EXCEEDED")
    return dict(value)


def make_prefix(*, helper_source: bytes, request: Mapping[str, Any], archive: Path, tool: Path) -> bytes:
    validated = _validate_request(dict(request))
    request_raw = canonical(validated)
    archive_digest, archive_bytes = _digest_path(archive)
    tool_digest, tool_bytes = _digest_path(tool)
    if archive_digest != validated["archive_sha256"]:
        raise RenderHelperError("archive digest does not match request", stage="archive", diagnostic_code="IDENTITY_MISMATCH")
    lengths = (len(helper_source), len(request_raw), tool_bytes, archive_bytes)
    if not (
        1 <= lengths[0] <= MAX_HELPER_BYTES
        and 1 <= lengths[1] <= MAX_REQUEST_BYTES
        and 1 <= lengths[2] <= MAX_TOOL_BYTES
        and 1 <= lengths[3] <= MAX_ARCHIVE_BYTES
    ):
        raise RenderHelperError("wire member exceeds its bound", stage="request", diagnostic_code="BOUND_EXCEEDED")
    return PREFIX.pack(
        MAGIC,
        VERSION,
        *lengths,
        bytes.fromhex(validated["request_sha256"].removeprefix("sha256:")),
        hashlib.sha256(helper_source).digest(),
        bytes.fromhex(tool_digest.removeprefix("sha256:")),
        bytes.fromhex(archive_digest.removeprefix("sha256:")),
    )


def packet(helper_source: bytes, request: Mapping[str, Any], archive: Path, *, tool: Path = GIT_PATH) -> bytes:
    prefix = make_prefix(helper_source=helper_source, request=request, archive=archive, tool=tool)
    return prefix + helper_source + canonical(dict(request)) + archive.read_bytes()


def parse_prefix(raw: bytes) -> WireBinding:
    if len(raw) != PREFIX.size:
        raise RenderHelperError("wire prefix is truncated", stage="request", diagnostic_code="VALIDATION_REFUSED")
    magic, version, helper_bytes, request_bytes, tool_bytes, archive_bytes, request_hash, helper_hash, tool_hash, archive_hash = PREFIX.unpack(raw)
    if magic != MAGIC or version != VERSION:
        raise RenderHelperError("wire prefix identity mismatch", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    if not (
        1 <= helper_bytes <= MAX_HELPER_BYTES
        and 1 <= request_bytes <= MAX_REQUEST_BYTES
        and 1 <= tool_bytes <= MAX_TOOL_BYTES
        and 1 <= archive_bytes <= MAX_ARCHIVE_BYTES
    ):
        raise RenderHelperError("wire length is outside bounds", stage="request", diagnostic_code="BOUND_EXCEEDED")
    return WireBinding(
        request_bytes=request_bytes,
        helper_bytes=helper_bytes,
        tool_bytes=tool_bytes,
        archive_bytes=archive_bytes,
        request_sha256="sha256:" + request_hash.hex(),
        helper_sha256="sha256:" + helper_hash.hex(),
        tool_sha256="sha256:" + tool_hash.hex(),
        archive_sha256="sha256:" + archive_hash.hex(),
    )


def validate_phase1_failure(value: Any, *, binding: WireBinding) -> dict[str, Any]:
    fields = {
        "schema",
        "outcome",
        "stage",
        "diagnostic_code",
        "request_sha256",
        "helper_sha256",
        "tool_sha256",
        "archive_sha256",
        "request_bytes",
        "helper_bytes",
        "tool_bytes",
        "archive_bytes",
        "cleanup",
        "effects",
        "receipt_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields or len(canonical(value)) > 4096:
        raise RenderHelperError("Phase1 failure schema invalid", stage="request", diagnostic_code="VALIDATION_REFUSED")
    unsigned = dict(value)
    claimed = unsigned.pop("receipt_sha256")
    expected_binding = {
        "request_sha256": binding.request_sha256,
        "helper_sha256": binding.helper_sha256,
        "tool_sha256": binding.tool_sha256,
        "archive_sha256": binding.archive_sha256,
        "request_bytes": binding.request_bytes,
        "helper_bytes": binding.helper_bytes,
        "tool_bytes": binding.tool_bytes,
        "archive_bytes": binding.archive_bytes,
    }
    if (
        value["schema"] != PHASE1_FAILURE_SCHEMA
        or value["outcome"] != "FAILED"
        or value["stage"] != "phase1-bootstrap"
        or value["diagnostic_code"]
        not in {
            "BAD_PREFIX",
            "BAD_LENGTH",
            "TRUNCATED_OR_TRAILING_BODY",
            "HELPER_HASH_MISMATCH",
            "ARCHIVE_HASH_MISMATCH",
            "BAD_REQUEST_JSON",
            "NONCANONICAL_REQUEST",
            "REQUEST_HASH_MISMATCH",
            "ARCHIVE_BINDING_MISMATCH",
        }
        or any(value[key] != expected for key, expected in expected_binding.items())
        or value["cleanup"] != "not-created"
        or value["effects"] != EFFECTS
        or claimed != _sha256(canonical(unsigned))
    ):
        raise RenderHelperError("Phase1 failure binding invalid", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    return dict(value)


def validate_terminal(value: Any, *, binding: WireBinding, request: Mapping[str, Any], allow_test_marker: bool = False) -> dict[str, Any]:
    """Validate a post-bootstrap terminal without treating the A1 hold as success."""
    validated = _validate_request(dict(request))
    if not isinstance(value, dict) or len(canonical(value)) > 16 * 1024:
        raise RenderHelperError("helper terminal is absent or oversized", stage="request", diagnostic_code="VALIDATION_REFUSED")
    unsigned = dict(value)
    claimed = unsigned.pop("receipt_sha256", None)
    common = {
        "request_sha256",
        "helper_sha256",
        "tool_sha256",
        "archive_sha256",
        "source_commit",
        "source_tree",
        "provider_sha256",
        "effects",
        "cleanup",
    }
    if not common <= set(unsigned) or claimed != _sha256(canonical(unsigned)):
        raise RenderHelperError("helper terminal self-hash is invalid", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    expected = {
        "request_sha256": binding.request_sha256,
        "helper_sha256": binding.helper_sha256,
        "tool_sha256": binding.tool_sha256,
        "archive_sha256": binding.archive_sha256,
        "source_commit": validated["source_commit"],
        "source_tree": validated["source_tree"],
        "provider_sha256": validated["provider_sha256"],
        "effects": EFFECTS,
    }
    if any(value[key] != expected_value for key, expected_value in expected.items()):
        raise RenderHelperError("helper terminal identity binding is invalid", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    schema = value.get("schema")
    if schema == HOLD_SCHEMA:
        fields = common | {"schema", "outcome", "stage", "verified_source_files", "executor", "receipt_sha256"}
        expected_files = {path: validated[field] for field, path in SOURCE_DIGEST_PATHS.items()}
        if (
            set(value) != fields
            or value["outcome"] != HOLD_OUTCOME
            or value["stage"] != "source-verified"
            or value["cleanup"] != "removed"
            or value["verified_source_files"] != expected_files
            or value["executor"] != {"installed": False, "reachable_from_request": False, "reachable_from_environment": False}
        ):
            raise RenderHelperError("source hold terminal is invalid", stage="request", diagnostic_code="VALIDATION_REFUSED")
    elif schema == FAILURE_SCHEMA:
        fields = common | {
            "schema",
            "outcome",
            "stage",
            "diagnostic_code",
            "original_stage",
            "original_diagnostic_code",
            "receipt_sha256",
        }
        if set(value) != fields or value["original_stage"] not in FAILURE_STAGES | {"source-verified"}:
            raise RenderHelperError("helper failure terminal is invalid", stage="request", diagnostic_code="VALIDATION_REFUSED")
        if value["outcome"] == "AMBIGUOUS":
            valid_state = (
                value["stage"],
                value["diagnostic_code"],
                value["cleanup"],
            ) == ("cleanup", "CLEANUP_FAILED", "failed") and (
                (value["original_stage"] == "source-verified" and value["original_diagnostic_code"] == "NONE")
                or (value["original_stage"] in FAILURE_STAGES - {"cleanup"} and value["original_diagnostic_code"] in FAILURE_CODES - {"CLEANUP_FAILED"})
            )
        else:
            valid_state = (
                value["outcome"] == "FAILED"
                and value["stage"] == value["original_stage"]
                and value["diagnostic_code"] == value["original_diagnostic_code"]
                and value["diagnostic_code"] in FAILURE_CODES - {"CLEANUP_FAILED"}
                and value["cleanup"] in {"removed", "not-created"}
            )
        if not valid_state:
            raise RenderHelperError("helper failure lifecycle is contradictory", stage="request", diagnostic_code="VALIDATION_REFUSED")
    elif schema == TEST_MARKER_SCHEMA and allow_test_marker:
        fields = common | {"schema", "outcome", "marker", "receipt_sha256"}
        if (
            set(value) != fields
            or value["outcome"] != "TEST_ONLY_SOURCE_VERIFIED"
            or value["cleanup"] != "removed"
            or not isinstance(value["marker"], str)
            or not 1 <= len(value["marker"].encode()) <= 256
        ):
            raise RenderHelperError("test marker terminal is invalid", stage="request", diagnostic_code="VALIDATION_REFUSED")
    else:
        raise RenderHelperError("helper terminal schema is unknown", stage="request", diagnostic_code="VALIDATION_REFUSED")
    return dict(value)


def _binding_from_globals() -> WireBinding:
    names = {
        "request_bytes": "_BOOTSTRAP_REQUEST_BYTES",
        "helper_bytes": "_BOOTSTRAP_HELPER_BYTES",
        "tool_bytes": "_BOOTSTRAP_TOOL_BYTES",
        "archive_bytes": "_BOOTSTRAP_ARCHIVE_BYTES",
        "request_sha256": "_BOOTSTRAP_REQUEST_SHA256",
        "helper_sha256": "_BOOTSTRAP_HELPER_SHA256",
        "tool_sha256": "_BOOTSTRAP_TOOL_SHA256",
        "archive_sha256": "_BOOTSTRAP_ARCHIVE_SHA256",
    }
    values = {field: globals().get(name) for field, name in names.items()}
    if any(value is None for value in values.values()):
        raise RenderHelperError("bootstrap binding is absent", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    return WireBinding(**values)


def _open_bound_tool(path: Path, binding: WireBinding) -> int:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RenderHelperError("fixed Git executable is unavailable", stage="tool", diagnostic_code="IDENTITY_MISMATCH") from exc
    try:
        metadata = os.fstat(descriptor)
        digest = hashlib.sha256()
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != binding.tool_bytes or "sha256:" + digest.hexdigest() != binding.tool_sha256:
            raise RenderHelperError("fixed Git executable identity mismatch", stage="tool", diagnostic_code="IDENTITY_MISMATCH")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _prepare_scratch_root(path: Path, *, expected_uid: int) -> tuple[int, int, bool]:
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = False
    try:
        try:
            metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            os.mkdir(path.name, 0o700, dir_fd=parent_fd)
            created = True
            metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != expected_uid or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise RenderHelperError("scratch root trust mismatch", stage="scratch", diagnostic_code="IDENTITY_MISMATCH")
        root_fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        if os.listdir(root_fd):
            os.close(root_fd)
            raise RenderHelperError("scratch root contains pre-existing state", stage="scratch", diagnostic_code="IDENTITY_MISMATCH")
        return parent_fd, root_fd, created
    except BaseException:
        if created:
            os.rmdir(path.name, dir_fd=parent_fd)
        os.close(parent_fd)
        raise


def _normalized_member(member: tarfile.TarInfo) -> tuple[str, tuple[str, ...]]:
    raw = member.name[:-1] if member.name.endswith("/") else member.name
    parts = tuple(raw.split("/"))
    path = PurePosixPath(raw)
    if (
        not raw
        or member.name.startswith("/")
        or "\\" in member.name
        or any(part in {"", ".", "..", ".git"} for part in parts)
        or path.as_posix() != raw
        or parts[0] != ARCHIVE_ROOT
        or not (member.isdir() or member.isreg())
        or set(member.pax_headers) != {"comment"}
    ):
        raise RenderHelperError("archive member is unsafe", stage="archive", diagnostic_code="VALIDATION_REFUSED")
    return raw, parts


def _safe_extract(archive: Path, target: Path, request: Mapping[str, Any]) -> Path:
    try:
        source = tarfile.open(archive, "r:")
    except (OSError, tarfile.TarError) as exc:
        raise RenderHelperError("archive is not a plain tar", stage="archive", diagnostic_code="VALIDATION_REFUSED") from exc
    with source:
        if source.pax_headers != {"comment": request["source_commit"]}:
            raise RenderHelperError("archive PAX commit is not exact", stage="archive", diagnostic_code="IDENTITY_MISMATCH")
        members = source.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise RenderHelperError("archive member bound exceeded", stage="archive", diagnostic_code="BOUND_EXCEEDED")
        normalized: list[str] = []
        directories: set[str] = set()
        total = 0
        for member in members:
            name, parts = _normalized_member(member)
            if member.pax_headers["comment"] != request["source_commit"]:
                raise RenderHelperError("archive member PAX commit differs", stage="archive", diagnostic_code="IDENTITY_MISMATCH")
            normalized.append(name)
            if member.isdir():
                directories.add(name)
            else:
                total += member.size
            if total > MAX_UNPACKED_BYTES:
                raise RenderHelperError("archive unpacked bound exceeded", stage="archive", diagnostic_code="BOUND_EXCEEDED")
            if len(parts) == 1 and (name != ARCHIVE_ROOT or not member.isdir()):
                raise RenderHelperError("archive root entry is not exact", stage="archive", diagnostic_code="VALIDATION_REFUSED")
        if len(normalized) != len(set(normalized)) or ARCHIVE_ROOT not in directories:
            raise RenderHelperError("archive paths are duplicate or rootless", stage="archive", diagnostic_code="VALIDATION_REFUSED")
        for name in normalized:
            parent = PurePosixPath(name).parent.as_posix()
            if parent != "." and parent not in directories:
                raise RenderHelperError("archive omits an explicit parent directory", stage="archive", diagnostic_code="VALIDATION_REFUSED")
        target.mkdir(mode=0o700)
        for member, name in sorted(zip(members, normalized, strict=True), key=lambda item: (len(PurePosixPath(item[1]).parts), not item[0].isdir(), item[1])):
            destination = target / name
            if member.isdir():
                destination.mkdir(mode=0o700)
                continue
            extracted = source.extractfile(member)
            if extracted is None:
                raise RenderHelperError("archive regular member has no payload", stage="archive", diagnostic_code="VALIDATION_REFUSED")
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755 if member.mode & 0o111 else 0o644)
            written = 0
            try:
                with extracted:
                    while block := extracted.read(1024 * 1024):
                        written += os.write(descriptor, block)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if written != member.size:
                raise RenderHelperError("archive member length changed", stage="archive", diagnostic_code="IDENTITY_MISMATCH")
    return target / ARCHIVE_ROOT


def _run_git(git_fd: int, args: list[str], *, source: Path, request: Mapping[str, Any]) -> str:
    executable = f"/proc/self/fd/{git_fd}"
    argv = [
        executable,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.autocrlf=false",
        "-c",
        "filter.lfs.smudge=",
        "-c",
        "filter.lfs.required=false",
        *args,
    ]
    try:
        result = subprocess.run(
            argv,
            cwd=source,
            env={"LC_ALL": "C", "PATH": "/no-ambient-path", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
            capture_output=True,
            check=False,
            timeout=min(120, request["max_duration_seconds"]),
            close_fds=True,
            pass_fds=(git_fd,),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderHelperError("fixed Git subprocess did not complete", stage="source", diagnostic_code="SUBPROCESS_FAILED") from exc
    if result.returncode != 0:
        raise RenderHelperError("fixed Git subprocess failed", stage="source", diagnostic_code="SUBPROCESS_FAILED")
    if max(len(result.stdout), len(result.stderr)) > request["max_output_bytes"]:
        raise RenderHelperError("fixed Git subprocess output exceeded bound", stage="source", diagnostic_code="BOUND_EXCEEDED")
    try:
        return result.stdout.decode()
    except UnicodeDecodeError as exc:
        raise RenderHelperError("fixed Git output is not UTF-8", stage="source", diagnostic_code="VALIDATION_REFUSED") from exc


def _verify_source(source: Path, *, request: Mapping[str, Any], git_fd: int) -> dict[str, Any]:
    _run_git(git_fd, ["init", "-q"], source=source, request=request)
    _run_git(git_fd, ["add", "-f", "-A"], source=source, request=request)
    observed_tree = _run_git(git_fd, ["write-tree"], source=source, request=request).strip()
    if observed_tree != request["source_tree"]:
        raise RenderHelperError("reconstructed source tree mismatch", stage="source", diagnostic_code="IDENTITY_MISMATCH")
    verified: dict[str, str] = {}
    for field, relative in SOURCE_DIGEST_PATHS.items():
        path = source / relative
        try:
            digest, _ = _digest_path(path)
        except (OSError, RenderHelperError) as exc:
            raise RenderHelperError("bound source file is unavailable", stage="source", diagnostic_code="IDENTITY_MISMATCH") from exc
        if digest != request[field]:
            raise RenderHelperError("bound source file digest mismatch", stage="source", diagnostic_code="IDENTITY_MISMATCH")
        verified[relative] = digest
    return verified


def _terminal_base(binding: WireBinding, request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "request_sha256": binding.request_sha256,
        "helper_sha256": binding.helper_sha256,
        "tool_sha256": binding.tool_sha256,
        "archive_sha256": binding.archive_sha256,
        "source_commit": request.get("source_commit", "unknown"),
        "source_tree": request.get("source_tree", "unknown"),
        "provider_sha256": request.get("provider_sha256", "unknown"),
        "effects": dict(EFFECTS),
    }


def _failure(
    binding: WireBinding,
    request: Mapping[str, Any],
    *,
    original_stage: str,
    original_code: str,
    cleanup: str,
) -> dict[str, Any]:
    ambiguous = cleanup == "failed"
    value = {
        "schema": FAILURE_SCHEMA,
        "outcome": "AMBIGUOUS" if ambiguous else "FAILED",
        "stage": "cleanup" if ambiguous else original_stage,
        "diagnostic_code": "CLEANUP_FAILED" if ambiguous else original_code,
        "original_stage": original_stage,
        "original_diagnostic_code": original_code,
        "cleanup": cleanup,
        **_terminal_base(binding, request),
    }
    value["receipt_sha256"] = _sha256(canonical(value))
    return value


def _hold(binding: WireBinding, request: Mapping[str, Any], verified: Mapping[str, str]) -> dict[str, Any]:
    value = {
        "schema": HOLD_SCHEMA,
        "outcome": HOLD_OUTCOME,
        "stage": "source-verified",
        "verified_source_files": dict(verified),
        "executor": {"installed": False, "reachable_from_request": False, "reachable_from_environment": False},
        "cleanup": "removed",
        **_terminal_base(binding, request),
    }
    value["receipt_sha256"] = _sha256(canonical(value))
    return value


def execute_packet(
    stream: BinaryIO,
    *,
    binding: WireBinding | None = None,
    git_path: Path | None = None,
    scratch_root: Path = SCRATCH_ROOT,
    scratch_uid: int | None = None,
    cleanup_tree: Callable[..., None] = shutil.rmtree,
    test_executor: Callable[[Path, Mapping[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """Verify source and return only a post-cleanup failure, hold, or test marker."""
    bound = binding or _binding_from_globals()
    request: dict[str, Any] = {}
    git_fd = parent_fd = root_fd = -1
    root_created = False
    run_path: Path | None = None
    original_stage = "request"
    original_code = "INTERNAL_ERROR"
    verified: dict[str, str] | None = None
    marker: str | None = None
    failure: BaseException | None = None
    try:
        size_raw = stream.read(8)
        if len(size_raw) != 8 or struct.unpack("!Q", size_raw)[0] != bound.request_bytes:
            raise RenderHelperError("helper request frame length mismatch", stage="request", diagnostic_code="IDENTITY_MISMATCH")
        request_raw = stream.read(bound.request_bytes)
        if len(request_raw) != bound.request_bytes:
            raise RenderHelperError("helper request frame is truncated", stage="request", diagnostic_code="VALIDATION_REFUSED")
        try:
            request = json.loads(request_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RenderHelperError("helper request JSON is invalid", stage="request", diagnostic_code="VALIDATION_REFUSED") from exc
        request = _validate_request(request)
        if canonical(request) != request_raw or request["request_sha256"] != bound.request_sha256 or request["archive_sha256"] != bound.archive_sha256:
            raise RenderHelperError("helper request/bootstrap binding mismatch", stage="request", diagnostic_code="IDENTITY_MISMATCH")
        helper_hash = globals().get("_BOOTSTRAP_HELPER_SHA256", bound.helper_sha256)
        if helper_hash != bound.helper_sha256:
            raise RenderHelperError("helper/bootstrap source identity mismatch", stage="request", diagnostic_code="IDENTITY_MISMATCH")
        selected_git = git_path or Path(globals().get("_TEST_ONLY_GIT_PATH", str(GIT_PATH)))
        git_fd = _open_bound_tool(selected_git, bound)
        parent_fd, root_fd, root_created = _prepare_scratch_root(scratch_root, expected_uid=os.geteuid() if scratch_uid is None else scratch_uid)
        run_name = "run-" + secrets.token_hex(16)
        run_path = scratch_root / run_name
        os.mkdir(run_name, 0o700, dir_fd=root_fd)
        archive = run_path / "source.tar"
        archive_fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        archive_hash = hashlib.sha256()
        received = 0
        try:
            while received < bound.archive_bytes:
                block = stream.read(min(1024 * 1024, bound.archive_bytes - received))
                if not block:
                    break
                archive_hash.update(block)
                offset = 0
                while offset < len(block):
                    offset += os.write(archive_fd, block[offset:])
                received += len(block)
            os.fsync(archive_fd)
        finally:
            os.close(archive_fd)
        if received != bound.archive_bytes or stream.read(1):
            raise RenderHelperError("helper archive length mismatch", stage="archive", diagnostic_code="BOUND_EXCEEDED")
        if "sha256:" + archive_hash.hexdigest() != bound.archive_sha256:
            raise RenderHelperError("helper archive digest mismatch", stage="archive", diagnostic_code="IDENTITY_MISMATCH")
        source = _safe_extract(archive, run_path / "unpacked", request)
        verified = _verify_source(source, request=request, git_fd=git_fd)
        if test_executor is not None:
            marker = test_executor(source, request)
            if not isinstance(marker, str) or not 1 <= len(marker.encode()) <= 256:
                raise RenderHelperError("test executor marker is invalid", stage="test-executor", diagnostic_code="VALIDATION_REFUSED")
    except BaseException as exc:
        failure = exc
        if isinstance(exc, RenderHelperError):
            original_stage, original_code = exc.stage, exc.diagnostic_code
        else:
            original_stage, original_code = "internal", "INTERNAL_ERROR"
    cleanup = "not-created"
    if run_path is not None and run_path.exists():
        try:
            cleanup_tree(run_path, ignore_errors=False)
            if run_path.exists():
                raise OSError("run scratch still exists")
            cleanup = "removed"
        except BaseException:
            cleanup = "failed"
    try:
        if root_fd >= 0:
            os.close(root_fd)
            root_fd = -1
        if root_created and cleanup in {"removed", "not-created"}:
            os.rmdir(scratch_root.name, dir_fd=parent_fd)
        if parent_fd >= 0:
            os.close(parent_fd)
            parent_fd = -1
    except BaseException:
        cleanup = "failed"
    finally:
        for descriptor in (git_fd, root_fd, parent_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    if cleanup == "failed":
        if failure is None:
            original_stage, original_code = "source-verified", "NONE"
        return _failure(bound, request, original_stage=original_stage, original_code=original_code, cleanup=cleanup)
    if failure is not None:
        return _failure(bound, request, original_stage=original_stage, original_code=original_code, cleanup=cleanup)
    if verified is None:
        return _failure(bound, request, original_stage="internal", original_code="INTERNAL_ERROR", cleanup=cleanup)
    if marker is not None:
        value = {
            "schema": TEST_MARKER_SCHEMA,
            "outcome": "TEST_ONLY_SOURCE_VERIFIED",
            "marker": marker,
            "cleanup": "removed",
            **_terminal_base(bound, request),
        }
        value["receipt_sha256"] = _sha256(canonical(value))
        return value
    return _hold(bound, request, verified)


def main(*, input_stream: BinaryIO | None = None, output_stream: BinaryIO | None = None) -> int:
    source = input_stream if input_stream is not None else sys.stdin.buffer
    sink = output_stream if output_stream is not None else sys.stdout.buffer
    try:
        value = execute_packet(source)
    except BaseException:
        # A production bootstrap always supplies the binding.  Reaching this
        # branch is deliberately not represented as a successful terminal.
        return 70
    sink.write(canonical(value))
    if value["schema"] == TEST_MARKER_SCHEMA:
        return 0
    if value["outcome"] == HOLD_OUTCOME:
        return 3
    return 2 if value["outcome"] == "AMBIGUOUS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
