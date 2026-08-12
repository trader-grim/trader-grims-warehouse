"""Standalone, source-bound observer render evaluator.

Phase A1 verifies the immutable source and its admitted Git artifact.  Only
that verified boundary can enter Phase A2, which performs one offline render
and delegates final artifact inspection to the source-bound provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

MAGIC = b"TGWNRH01"
VERSION = 1
PREFIX = struct.Struct("!8sIQQQQ32s32s32s32s")
MAX_HELPER_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 64 * 1024
MAX_TOOL_DESCRIPTOR_BYTES = 4096
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
ARCHIVE_ROOT = "trader-grims-warehouse"
SCRATCH_ROOT = Path("/var/tmp/tgw-nix-observer-render-helper")

REQUEST_SCHEMA = "tgw-nix-observer-render-evaluation-request/v1"
TARGET = "nix-input-observer-rendered-artifacts"
PHASE1_FAILURE_SCHEMA = "tgw-nix-observer-render-helper-phase1-failure/v1"
FAILURE_SCHEMA = "tgw-nix-observer-render-helper-failure/v1"
HOLD_SCHEMA = "tgw-nix-observer-render-source-hold/v1"
TEST_MARKER_SCHEMA = "tgw-nix-observer-render-test-marker/v1"
TOOL_DESCRIPTOR_SCHEMA = "tgw-nix-observer-render-source-tool/v2"
SUCCESS_SCHEMA = "tgw-nix-observer-render-helper-success/v1"
A2_FAILURE_SCHEMA = "tgw-nix-observer-render-helper-a2-failure/v1"
HOLD_OUTCOME = "SOURCE_VERIFIED_NO_EXECUTOR"

# The prerequisite receipt authorizes this resolved Nix-store Git artifact.
# Its SHA-256 fixes the complete byte string (and therefore its byte count);
# the explicit count below was recovered from that exact store artifact.
AUTHORIZED_TOOL_RECEIPT_SHA256 = "sha256:3a91e1a8824fa3f1d3bf563155d6af7d13f32d46e67f69146f80253aaf79cce6"
AUTHORIZED_GIT_PATH = "/nix/store/jg9g0gs0x0f69m8mn31syic45bf9lwh9-git-2.50.1/bin/git"
AUTHORIZED_GIT_SHA256 = "sha256:7caeec432b21191b6227a0150dedcf4b5503d6a4819d8ef7b223fbf1128da5f8"
AUTHORIZED_GIT_BYTES = 4_373_016
AUTHORIZED_GIT_OWNER_UID = 0
AUTHORIZED_GIT_MODE = 0o555

# Cross-bound, immutable Phase-A2 host prerequisites.  The first receipt
# admits the Nix executables; the second admits the exact input NAR and full
# systemd-analyze identity/version evidence used by the request.
AUTHORIZED_RENDER_RECEIPT_SHA256 = "sha256:820e95594e9825d6b10bd8d0dcbf9ba8d0dd3e3656132333351fd3121fce01ad"
AUTHORIZED_NIX_PATH = "/nix/store/mxafjxh0amr24d2gb7n3km6hljj79qsj-nix-2.28.5/bin/nix"
AUTHORIZED_NIX_SHA256 = "sha256:8fadf78aa447b028410e9840f1f971a860bfdcf02a9205d9282455f21f21221b"
# The prerequisite deliberately records the same multi-call artifact for
# nix-store.  It is invoked with an explicit nix-store argv[0].
AUTHORIZED_NIX_STORE_PATH = AUTHORIZED_NIX_PATH
AUTHORIZED_NIX_STORE_SHA256 = AUTHORIZED_NIX_SHA256
AUTHORIZED_SYSTEMD_ANALYZE_PATH = "/nix/store/kiplbb6yv7rmjf21hf9ky01b9kmgmnqn-systemd-257.10/bin/systemd-analyze"
AUTHORIZED_SYSTEMD_ANALYZE_SHA256 = "sha256:28c62cb24a08bebc45ce138078d4b3a3d3f47bcbfea3bf92d9b3dddfcd40bce3"
AUTHORIZED_SYSTEMD_ANALYZE_VERSION = "systemd 257 (257.10)"
AUTHORIZED_SYSTEMD_ANALYZE_VERSION_STDOUT_SHA256 = "sha256:17b46079431e2ab064d9664c917614c6a44b8a738f49881bdeb307c7ba70c1c5"
AUTHORIZED_SYSTEMD_ANALYZE_VERSION_STDOUT_BYTES = 338
AUTHORIZED_INPUT_PATH = "/nix/store/3p306srz83h9z9v0ma9xcxb8y8cdxkxj-source"
AUTHORIZED_INPUT_NAR_SHA256 = "sha256:d7a2a481f758aa369e44605a62c36b84f45110db34ab3910554a28347b68cb6c"
RENDER_ATTR = "packages.x86_64-linux.nix-input-observer-rendered-artifacts"
RENDER_EFFECT = {
    "kind": "offline-nix-observer-render",
    "target": RENDER_ATTR,
    "build": True,
    "activation": False,
    "deployment": False,
    "profile_write": False,
    "home_db_write": False,
    "live_flake_write": False,
    "network": False,
}
NIX_ARGV_PREFIX = (
    "--offline",
    "--option",
    "substituters",
    "",
    "--option",
    "trusted-public-keys",
    "",
    "--option",
    "builders",
    "",
    "--option",
    "builders-use-substitutes",
    "false",
    "--option",
    "allow-import-from-derivation",
    "false",
    "--option",
    "pure-eval",
    "true",
    "--option",
    "restrict-eval",
    "true",
    "--option",
    "sandbox",
    "true",
    "--option",
    "sandbox-fallback",
    "false",
    "--option",
    "flake-registry",
    "",
    "--no-write-lock-file",
)
NIX_CONFIG = (
    "experimental-features = nix-command flakes\n"
    "substituters =\n"
    "trusted-public-keys =\n"
    "builders =\n"
    "builders-use-substitutes = false\n"
    "allow-import-from-derivation = false\n"
    "pure-eval = true\n"
    "restrict-eval = true\n"
    "sandbox = true\n"
    "sandbox-fallback = false\n"
    "flake-registry =\n"
    "connect-timeout = 1\n"
    "fallback = false\n"
)

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

# This is the production bootstrap passed to isolated Python.  Its only test seam
# is a callable inserted directly into the bootstrap globals by an in-process
# test before execution; environment, request, archive and helper globals cannot
# select it.
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
 d.update({"request_sha256":hx("request"),"helper_sha256":hx("helper"),"tool_descriptor_sha256":hx("tool"),"archive_sha256":hx("archive")})
 d.update({"request_bytes":b.get("request_bytes",-1),"helper_bytes":b.get("helper_bytes",-1),"tool_descriptor_bytes":b.get("tool_bytes",-1),"archive_bytes":b.get("archive_bytes",-1)})
 d.update({"cleanup":"not-created","effects":{"build_attempted":False,"activation":False,"deployment":False,"profile_write":False,"home_db_write":False,"live_flake_write":False,"network":False}})
 d["receipt_sha256"]="sha256:"+hashlib.sha256(canon(d)).hexdigest()
 sys.stdout.buffer.write(canon(d)); raise SystemExit(1)
if len(raw)!=P.size: fail("TRUNCATED_PREFIX")
magic,version,hlen,rlen,tlen,alen,rh,hh,th,ah=P.unpack(raw)
b={"request":rh,"helper":hh,"tool":th,"archive":ah,"request_bytes":rlen,"helper_bytes":hlen,"tool_bytes":tlen,"archive_bytes":alen}
if magic!=b"TGWNRH01" or version!=1: fail("BAD_PREFIX",b)
if not (1<=hlen<=1048576 and 1<=rlen<=65536 and 1<=tlen<=4096 and 1<=alen<=134217728): fail("BAD_LENGTH",b)
helper=sys.stdin.buffer.read(hlen); request_raw=sys.stdin.buffer.read(rlen); tool_raw=sys.stdin.buffer.read(tlen); archive=sys.stdin.buffer.read(alen); extra=sys.stdin.buffer.read(1)
if len(helper)!=hlen or len(request_raw)!=rlen or len(tool_raw)!=tlen or len(archive)!=alen or extra: fail("TRUNCATED_OR_TRAILING_BODY",b)
if hashlib.sha256(helper).digest()!=hh: fail("HELPER_HASH_MISMATCH",b)
if hashlib.sha256(tool_raw).digest()!=th: fail("TOOL_DESCRIPTOR_HASH_MISMATCH",b)
if hashlib.sha256(archive).digest()!=ah: fail("ARCHIVE_HASH_MISMATCH",b)
try: request=json.loads(request_raw)
except Exception: fail("BAD_REQUEST_JSON",b)
if canon(request)!=request_raw or not isinstance(request,dict): fail("NONCANONICAL_REQUEST",b)
try: tool=json.loads(tool_raw)
except Exception: fail("BAD_TOOL_DESCRIPTOR_JSON",b)
tool_fields={"schema","name","request_sha256","authority_receipt_sha256","path","sha256","bytes","owner_uid","mode"}
if canon(tool)!=tool_raw or not isinstance(tool,dict) or set(tool)!=tool_fields: fail("BAD_TOOL_DESCRIPTOR",b)
if tool.get("schema")!="tgw-nix-observer-render-source-tool/v2" or tool.get("name")!="git": fail("BAD_TOOL_DESCRIPTOR",b)
if not isinstance(tool.get("path"),str) or not tool["path"].startswith("/") or "\x00" in tool["path"] or "\n" in tool["path"] or "\r" in tool["path"]: fail("BAD_TOOL_DESCRIPTOR",b)
if any(not isinstance(tool.get(k),str) or len(tool[k])!=71 or not tool[k].startswith("sha256:") for k in ("request_sha256","authority_receipt_sha256","sha256")): fail("BAD_TOOL_DESCRIPTOR",b)
if not isinstance(tool.get("bytes"),int) or isinstance(tool["bytes"],bool) or not 1<=tool["bytes"]<=67108864: fail("BAD_TOOL_DESCRIPTOR",b)
if not isinstance(tool.get("owner_uid"),int) or isinstance(tool["owner_uid"],bool) or tool["owner_uid"]<0 or not isinstance(tool.get("mode"),str) or len(tool["mode"])!=4: fail("BAD_TOOL_DESCRIPTOR",b)
claimed=request.get("request_sha256"); unsigned=dict(request); unsigned.pop("request_sha256",None)
if claimed!="sha256:"+rh.hex() or hashlib.sha256(canon(unsigned)).digest()!=rh: fail("REQUEST_HASH_MISMATCH",b)
if request.get("archive_sha256")!="sha256:"+ah.hex(): fail("ARCHIVE_BINDING_MISMATCH",b)
if tool.get("request_sha256")!=claimed: fail("TOOL_REQUEST_BINDING_MISMATCH",b)
g={"__name__":"__main__","_BOOTSTRAP_REQUEST_SHA256":"sha256:"+rh.hex(),"_BOOTSTRAP_HELPER_SHA256":"sha256:"+hh.hex(),"_BOOTSTRAP_TOOL_SHA256":"sha256:"+th.hex(),"_BOOTSTRAP_ARCHIVE_SHA256":"sha256:"+ah.hex(),"_BOOTSTRAP_REQUEST_BYTES":rlen,"_BOOTSTRAP_HELPER_BYTES":hlen,"_BOOTSTRAP_TOOL_BYTES":tlen,"_BOOTSTRAP_ARCHIVE_BYTES":alen}
test_executor=globals().get("_TEST_ONLY_EXECUTOR")
test_tool_authority=globals().get("_TEST_ONLY_TOOL_AUTHORITY")
test_a2_authority=globals().get("_TEST_ONLY_A2_AUTHORITY")
test_cleanup=globals().get("_TEST_ONLY_CLEANUP_TREE")
test_scratch=globals().get("_TEST_ONLY_SCRATCH_ROOT")
if test_executor is not None or test_tool_authority is not None or test_a2_authority is not None or test_cleanup is not None or test_scratch is not None: g["_BOOTSTRAP_DEFER_MAIN"]=True
sys.stdin=io.TextIOWrapper(io.BytesIO(struct.pack("!Q",rlen)+request_raw+struct.pack("!Q",tlen)+tool_raw+archive))
exec(compile(helper,"<tgw-nix-observer-render-helper>","exec"),g)
if test_executor is not None or test_tool_authority is not None or test_a2_authority is not None or test_cleanup is not None or test_scratch is not None:
 if test_tool_authority is not None: test_tool_authority=g["ToolAuthority"](**test_tool_authority)
 if test_a2_authority is not None: test_a2_authority=g["A2Authority"](**test_a2_authority)
 value=g["execute_packet"](
  sys.stdin.buffer,
  _test_executor=test_executor,
  _test_tool_authority=test_tool_authority,
  _test_a2_authority=test_a2_authority,
  cleanup_tree=test_cleanup or g["shutil"].rmtree,
  scratch_root=g["Path"](test_scratch) if test_scratch is not None else g["SCRATCH_ROOT"],
 )
 sys.stdout.buffer.write(g["canonical"](value))
 if value.get("schema") in {"tgw-nix-observer-render-test-marker/v1","tgw-nix-observer-render-helper-success/v1"}: code=0
 elif value.get("outcome")=="SOURCE_VERIFIED_NO_EXECUTOR": code=3
 elif value.get("outcome")=="AMBIGUOUS": code=2
 else: code=1
 raise SystemExit(code)
'''


class RenderHelperError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        stage: str,
        diagnostic_code: str,
        subprocess_step: str | None = None,
        return_code: int | None = None,
    ):
        super().__init__(message)
        self.stage = stage
        self.diagnostic_code = diagnostic_code
        self.subprocess_step = subprocess_step
        self.return_code = return_code


@dataclass(frozen=True)
class WireBinding:
    request_bytes: int
    helper_bytes: int
    tool_descriptor_bytes: int
    archive_bytes: int
    request_sha256: str
    helper_sha256: str
    tool_descriptor_sha256: str
    archive_sha256: str


@dataclass(frozen=True)
class HeldIdentity:
    device: int
    inode: int
    size: int
    mode: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class ToolAuthority:
    authority_receipt_sha256: str
    path: str
    sha256: str
    bytes: int
    owner_uid: int
    mode: int
    require_nix_store: bool = True
    forbid_owner_write: bool = True
    allow_mutable_parents: bool = False


@dataclass(frozen=True)
class ToolPathIdentity:
    device: int
    inode: int
    size: int
    mode: int
    owner_uid: int
    owner_gid: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class HeldTool:
    descriptor: int
    identity: ToolPathIdentity
    component_descriptors: tuple[int, ...]
    component_identities: tuple[ToolPathIdentity, ...]


@dataclass(frozen=True)
class A2Authority:
    a1_prerequisite_receipt_sha256: str
    a2_prerequisite_receipt_sha256: str
    nix_path: str
    nix_sha256: str
    nix_store_path: str
    nix_store_sha256: str
    systemd_analyze_path: str
    systemd_analyze_sha256: str
    systemd_analyze_version: str
    systemd_analyze_version_stdout_sha256: str
    systemd_analyze_version_stdout_bytes: int
    input_path: str
    input_nar_sha256: str
    store_root: str = "/nix/store"
    owner_uid: int = 0
    mode: int = 0o555
    require_nix_store: bool = True
    allow_mutable_parents: bool = False
    held_input_path: str = ""
    derivation_store_root: str = "/nix/store"
    scratch_root: str = str(SCRATCH_ROOT)


PRODUCTION_GIT_AUTHORITY = ToolAuthority(
    authority_receipt_sha256=AUTHORIZED_TOOL_RECEIPT_SHA256,
    path=AUTHORIZED_GIT_PATH,
    sha256=AUTHORIZED_GIT_SHA256,
    bytes=AUTHORIZED_GIT_BYTES,
    owner_uid=AUTHORIZED_GIT_OWNER_UID,
    mode=AUTHORIZED_GIT_MODE,
)

PRODUCTION_A2_AUTHORITY = A2Authority(
    a1_prerequisite_receipt_sha256=AUTHORIZED_TOOL_RECEIPT_SHA256,
    a2_prerequisite_receipt_sha256=AUTHORIZED_RENDER_RECEIPT_SHA256,
    nix_path=AUTHORIZED_NIX_PATH,
    nix_sha256=AUTHORIZED_NIX_SHA256,
    nix_store_path=AUTHORIZED_NIX_STORE_PATH,
    nix_store_sha256=AUTHORIZED_NIX_STORE_SHA256,
    systemd_analyze_path=AUTHORIZED_SYSTEMD_ANALYZE_PATH,
    systemd_analyze_sha256=AUTHORIZED_SYSTEMD_ANALYZE_SHA256,
    systemd_analyze_version=AUTHORIZED_SYSTEMD_ANALYZE_VERSION,
    systemd_analyze_version_stdout_sha256=AUTHORIZED_SYSTEMD_ANALYZE_VERSION_STDOUT_SHA256,
    systemd_analyze_version_stdout_bytes=AUTHORIZED_SYSTEMD_ANALYZE_VERSION_STDOUT_BYTES,
    input_path=AUTHORIZED_INPUT_PATH,
    input_nar_sha256=AUTHORIZED_INPUT_NAR_SHA256,
)


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


def _identity(metadata: os.stat_result) -> HeldIdentity:
    return HeldIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        mode=metadata.st_mode,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
    )


def _tool_path_identity(metadata: os.stat_result) -> ToolPathIdentity:
    return ToolPathIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        mode=metadata.st_mode,
        owner_uid=metadata.st_uid,
        owner_gid=metadata.st_gid,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
    )


def _validate_tool_component(
    metadata: os.stat_result,
    *,
    final: bool,
    authority: ToolAuthority,
    allow_owner_write: bool = False,
    allow_group_write: bool = False,
    allow_world_write: bool = False,
    require_sticky: bool = False,
) -> None:
    expected_kind = stat.S_ISREG if final else stat.S_ISDIR
    forbidden_writes = (0 if allow_world_write else 0o002) | (0 if allow_group_write else 0o020) | (0 if allow_owner_write else 0o200)
    if (
        not expected_kind(metadata.st_mode)
        or ((final or not authority.allow_mutable_parents) and metadata.st_uid != authority.owner_uid)
        or metadata.st_mode & forbidden_writes
        or (require_sticky and not metadata.st_mode & stat.S_ISVTX)
        or (
            final
            and (
                stat.S_IMODE(metadata.st_mode) != authority.mode
                or (authority.forbid_owner_write and metadata.st_mode & 0o200)
                or not metadata.st_mode & 0o111
            )
        )
    ):
        raise RenderHelperError("tool path ownership or mode is mutable", stage="tool", diagnostic_code="IDENTITY_MISMATCH")


def _open_resolved_regular(path: Path, *, authority: ToolAuthority) -> HeldTool:
    raw = str(path)
    pure = PurePosixPath(raw)
    if not pure.is_absolute() or pure.as_posix() != raw or any(part in {"", ".", ".."} for part in pure.parts[1:]):
        raise RenderHelperError("tool path is not exact and absolute", stage="tool", diagnostic_code="VALIDATION_REFUSED")
    if authority.require_nix_store and not re.fullmatch(
        r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+/bin/[A-Za-z0-9+._?=-]+", raw
    ):
        raise RenderHelperError("tool path is outside the immutable Nix store", stage="tool", diagnostic_code="VALIDATION_REFUSED")
    directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    component_descriptors = [directory_fd]
    component_identities: list[ToolPathIdentity] = []
    try:
        root_metadata = os.fstat(directory_fd)
        _validate_tool_component(root_metadata, final=False, authority=authority, allow_owner_write=True)
        component_identities.append(_tool_path_identity(root_metadata))
        walked: list[str] = []
        for part in pure.parts[1:-1]:
            walked.append(part)
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            directory_fd = next_fd
            component_descriptors.append(directory_fd)
            metadata = os.fstat(directory_fd)
            store_root = authority.require_nix_store and walked == ["nix", "store"]
            immutable_store_member = authority.require_nix_store and len(walked) >= 3
            _validate_tool_component(
                metadata,
                final=False,
                authority=authority,
                allow_owner_write=not immutable_store_member,
                allow_group_write=store_root or authority.allow_mutable_parents,
                allow_world_write=authority.allow_mutable_parents,
                require_sticky=store_root and bool(metadata.st_mode & 0o020),
            )
            component_identities.append(_tool_path_identity(metadata))
        descriptor = os.open(pure.parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        try:
            metadata = os.fstat(descriptor)
            _validate_tool_component(
                metadata,
                final=True,
                authority=authority,
                allow_owner_write=not authority.forbid_owner_write,
            )
            return HeldTool(
                descriptor=descriptor,
                identity=_tool_path_identity(metadata),
                component_descriptors=tuple(component_descriptors),
                component_identities=tuple(component_identities),
            )
        except BaseException:
            os.close(descriptor)
            raise
    except (OSError, RenderHelperError) as exc:
        for component_fd in reversed(component_descriptors):
            os.close(component_fd)
        if isinstance(exc, RenderHelperError):
            raise
        raise RenderHelperError("tool path is not a resolved regular artifact", stage="tool", diagnostic_code="IDENTITY_MISMATCH") from exc


def _read_held(descriptor: int, *, stage: str, max_bytes: int) -> tuple[bytes, HeldIdentity]:
    before_stat = os.fstat(descriptor)
    before = _identity(before_stat)
    if not stat.S_ISREG(before.mode):
        raise RenderHelperError("held artifact is not regular", stage=stage, diagnostic_code="IDENTITY_MISMATCH")
    if not 1 <= before.size <= max_bytes:
        raise RenderHelperError("held artifact size exceeds its bound", stage=stage, diagnostic_code="BOUND_EXCEEDED")
    content = bytearray()
    while block := os.read(descriptor, 1024 * 1024):
        content.extend(block)
    after = _identity(os.fstat(descriptor))
    if before != after or len(content) != before.size:
        raise RenderHelperError("held artifact changed while read", stage=stage, diagnostic_code="IDENTITY_MISMATCH")
    os.lseek(descriptor, 0, os.SEEK_SET)
    return bytes(content), before


def _expected_tool_descriptor(request_sha256: str, authority: ToolAuthority) -> dict[str, Any]:
    return {
        "schema": TOOL_DESCRIPTOR_SCHEMA,
        "name": "git",
        "request_sha256": request_sha256,
        "authority_receipt_sha256": authority.authority_receipt_sha256,
        "path": authority.path,
        "sha256": authority.sha256,
        "bytes": authority.bytes,
        "owner_uid": authority.owner_uid,
        "mode": f"{authority.mode:04o}",
    }


def _close_held_tool(tool: HeldTool) -> None:
    for descriptor in (tool.descriptor, *reversed(tool.component_descriptors)):
        try:
            os.close(descriptor)
        except OSError:
            pass


def describe_tool(
    request: Mapping[str, Any],
    *,
    _test_tool_authority: ToolAuthority | None = None,
    _production_tool_authority: ToolAuthority = PRODUCTION_GIT_AUTHORITY,
) -> dict[str, Any]:
    """Describe only the admitted Git artifact and bind it to one request."""
    validated_request = _validate_request(dict(request))
    authority = _production_tool_authority if _test_tool_authority is None else _test_tool_authority
    held = _open_resolved_regular(Path(authority.path), authority=authority)
    try:
        raw, identity = _read_held(held.descriptor, stage="tool", max_bytes=64 * 1024 * 1024)
        expected = _expected_tool_descriptor(validated_request["request_sha256"], authority)
        if identity.size != authority.bytes or _sha256(raw) != authority.sha256:
            raise RenderHelperError("admitted Git artifact identity mismatch", stage="tool", diagnostic_code="IDENTITY_MISMATCH")
        return expected
    finally:
        _close_held_tool(held)


def _validate_tool_descriptor(
    value: Any,
    *,
    request_sha256: str | None = None,
    authority: ToolAuthority | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "name",
            "request_sha256",
            "authority_receipt_sha256",
            "path",
            "sha256",
            "bytes",
            "owner_uid",
            "mode",
        }
        or value["schema"] != TOOL_DESCRIPTOR_SCHEMA
        or value["name"] != "git"
        or not isinstance(value["request_sha256"], str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["request_sha256"])
        or not isinstance(value["authority_receipt_sha256"], str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["authority_receipt_sha256"])
        or not isinstance(value["path"], str)
        or not PurePosixPath(value["path"]).is_absolute()
        or PurePosixPath(value["path"]).as_posix() != value["path"]
        or any(part in {"", ".", ".."} for part in PurePosixPath(value["path"]).parts[1:])
        or not isinstance(value["sha256"], str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["sha256"])
        or type(value["bytes"]) is not int
        or not 1 <= value["bytes"] <= 64 * 1024 * 1024
        or type(value["owner_uid"]) is not int
        or value["owner_uid"] < 0
        or not isinstance(value["mode"], str)
        or not re.fullmatch(r"0[0-7]{3}", value["mode"])
    ):
        raise RenderHelperError("source tool descriptor is invalid", stage="tool", diagnostic_code="VALIDATION_REFUSED")
    if request_sha256 is not None and value["request_sha256"] != request_sha256:
        raise RenderHelperError("source tool is not composed with this request", stage="tool", diagnostic_code="IDENTITY_MISMATCH")
    if authority is not None and value != _expected_tool_descriptor(value["request_sha256"], authority):
        raise RenderHelperError("source tool is not the admitted Git identity", stage="tool", diagnostic_code="IDENTITY_MISMATCH")
    return dict(value)


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


def make_prefix(
    *,
    helper_source: bytes,
    request: Mapping[str, Any],
    tool_descriptor: Mapping[str, Any],
    archive_raw: bytes,
    _test_tool_authority: ToolAuthority | None = None,
    _production_tool_authority: ToolAuthority = PRODUCTION_GIT_AUTHORITY,
) -> bytes:
    validated = _validate_request(dict(request))
    authority = _production_tool_authority if _test_tool_authority is None else _test_tool_authority
    tool = _validate_tool_descriptor(
        dict(tool_descriptor), request_sha256=validated["request_sha256"], authority=authority
    )
    request_raw = canonical(validated)
    tool_raw = canonical(tool)
    archive_digest = _sha256(archive_raw)
    if archive_digest != validated["archive_sha256"]:
        raise RenderHelperError("archive digest does not match request", stage="archive", diagnostic_code="IDENTITY_MISMATCH")
    lengths = (len(helper_source), len(request_raw), len(tool_raw), len(archive_raw))
    if not (
        1 <= lengths[0] <= MAX_HELPER_BYTES
        and 1 <= lengths[1] <= MAX_REQUEST_BYTES
        and 1 <= lengths[2] <= MAX_TOOL_DESCRIPTOR_BYTES
        and 1 <= lengths[3] <= MAX_ARCHIVE_BYTES
    ):
        raise RenderHelperError("wire member exceeds its bound", stage="request", diagnostic_code="BOUND_EXCEEDED")
    return PREFIX.pack(
        MAGIC,
        VERSION,
        *lengths,
        bytes.fromhex(validated["request_sha256"].removeprefix("sha256:")),
        hashlib.sha256(helper_source).digest(),
        hashlib.sha256(tool_raw).digest(),
        bytes.fromhex(archive_digest.removeprefix("sha256:")),
    )


def packet(
    helper_source: bytes,
    request: Mapping[str, Any],
    archive: Path,
    *,
    tool_descriptor: Mapping[str, Any] | None = None,
    _test_tool_authority: ToolAuthority | None = None,
    _production_tool_authority: ToolAuthority = PRODUCTION_GIT_AUTHORITY,
) -> bytes:
    """Frame one packet from held, admitted tool and archive inodes."""
    authority = _production_tool_authority if _test_tool_authority is None else _test_tool_authority
    observed_tool = describe_tool(
        request,
        _test_tool_authority=_test_tool_authority,
        _production_tool_authority=_production_tool_authority,
    )
    if tool_descriptor is not None and dict(tool_descriptor) != observed_tool:
        raise RenderHelperError("supplied tool differs from admitted held artifact", stage="tool", diagnostic_code="IDENTITY_MISMATCH")
    archive_fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        archive_raw, _ = _read_held(archive_fd, stage="archive", max_bytes=MAX_ARCHIVE_BYTES)
    finally:
        os.close(archive_fd)
    tool_raw = canonical(observed_tool)
    prefix = make_prefix(
        helper_source=helper_source,
        request=request,
        tool_descriptor=observed_tool,
        archive_raw=archive_raw,
        _test_tool_authority=authority,
    )
    return prefix + helper_source + canonical(dict(request)) + tool_raw + archive_raw


def parse_prefix(raw: bytes) -> WireBinding:
    if len(raw) != PREFIX.size:
        raise RenderHelperError("wire prefix is truncated", stage="request", diagnostic_code="VALIDATION_REFUSED")
    magic, version, helper_bytes, request_bytes, tool_bytes, archive_bytes, request_hash, helper_hash, tool_hash, archive_hash = PREFIX.unpack(raw)
    if magic != MAGIC or version != VERSION:
        raise RenderHelperError("wire prefix identity mismatch", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    if not (
        1 <= helper_bytes <= MAX_HELPER_BYTES
        and 1 <= request_bytes <= MAX_REQUEST_BYTES
        and 1 <= tool_bytes <= MAX_TOOL_DESCRIPTOR_BYTES
        and 1 <= archive_bytes <= MAX_ARCHIVE_BYTES
    ):
        raise RenderHelperError("wire length is outside bounds", stage="request", diagnostic_code="BOUND_EXCEEDED")
    return WireBinding(
        request_bytes=request_bytes,
        helper_bytes=helper_bytes,
        tool_descriptor_bytes=tool_bytes,
        archive_bytes=archive_bytes,
        request_sha256="sha256:" + request_hash.hex(),
        helper_sha256="sha256:" + helper_hash.hex(),
        tool_descriptor_sha256="sha256:" + tool_hash.hex(),
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
        "tool_descriptor_sha256",
        "archive_sha256",
        "request_bytes",
        "helper_bytes",
        "tool_descriptor_bytes",
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
        "tool_descriptor_sha256": binding.tool_descriptor_sha256,
        "archive_sha256": binding.archive_sha256,
        "request_bytes": binding.request_bytes,
        "helper_bytes": binding.helper_bytes,
        "tool_descriptor_bytes": binding.tool_descriptor_bytes,
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
            "TOOL_DESCRIPTOR_HASH_MISMATCH",
            "ARCHIVE_HASH_MISMATCH",
            "BAD_REQUEST_JSON",
            "BAD_TOOL_DESCRIPTOR_JSON",
            "BAD_TOOL_DESCRIPTOR",
            "NONCANONICAL_REQUEST",
            "REQUEST_HASH_MISMATCH",
            "ARCHIVE_BINDING_MISMATCH",
            "TOOL_REQUEST_BINDING_MISMATCH",
        }
        or any(value[key] != expected for key, expected in expected_binding.items())
        or value["cleanup"] != "not-created"
        or value["effects"] != EFFECTS
        or claimed != _sha256(canonical(unsigned))
    ):
        raise RenderHelperError("Phase1 failure binding invalid", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    return dict(value)


def validate_terminal(
    value: Any,
    *,
    binding: WireBinding,
    request: Mapping[str, Any],
    tool_descriptor: Mapping[str, Any],
    allow_test_marker: bool = False,
    _test_tool_authority: ToolAuthority | None = None,
    _production_tool_authority: ToolAuthority = PRODUCTION_GIT_AUTHORITY,
) -> dict[str, Any]:
    """Validate a closed post-bootstrap A1 or A2 terminal."""
    validated = _validate_request(dict(request))
    authority = _production_tool_authority if _test_tool_authority is None else _test_tool_authority
    a2_authority = PRODUCTION_A2_AUTHORITY
    tool = _validate_tool_descriptor(dict(tool_descriptor), request_sha256=validated["request_sha256"])
    if not isinstance(value, dict) or len(canonical(value)) > 16 * 1024:
        if not isinstance(value, dict) or len(canonical(value)) > validated["max_output_bytes"]:
            raise RenderHelperError("helper terminal is absent or oversized", stage="request", diagnostic_code="VALIDATION_REFUSED")
    if value.get("schema") in {SUCCESS_SCHEMA, A2_FAILURE_SCHEMA}:
        if _test_tool_authority is not None:
            # Test callers which use a non-production A1 identity must supply
            # their A2 authority through the dedicated validator.
            raise RenderHelperError("A2 terminal requires explicit A2 authority", stage="request", diagnostic_code="VALIDATION_REFUSED")
        return validate_a2_terminal(
            value,
            binding=binding,
            request=validated,
            tool_descriptor=tool,
            authority=a2_authority,
        )
    unsigned = dict(value)
    claimed = unsigned.pop("receipt_sha256", None)
    common = {
        "request_sha256",
        "helper_sha256",
        "tool_descriptor_sha256",
        "tool",
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
        "tool_descriptor_sha256": binding.tool_descriptor_sha256,
        "tool": tool,
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
        _validate_tool_descriptor(tool, request_sha256=validated["request_sha256"], authority=authority)
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
        _validate_tool_descriptor(tool, request_sha256=validated["request_sha256"], authority=authority)
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


def _validate_provider_receipt(value: Any, *, request: Mapping[str, Any], authority: A2Authority) -> dict[str, Any]:
    fields = {
        "schema",
        "request_sha256",
        "outcome",
        "metadata_status",
        "files",
        "output_root",
        "evaluated_drv",
        "drv_output",
        "output_manifest_sha256",
        "systemd_verify",
        "cleanup",
        "effects",
        "receipt_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RenderHelperError("provider receipt schema is not closed", stage="request", diagnostic_code="VALIDATION_REFUSED")
    unsigned = dict(value)
    claimed = unsigned.pop("receipt_sha256")
    files = value["files"]
    verify = value["systemd_verify"]
    store_root = Path(authority.store_root)
    if (
        claimed != _sha256(canonical(unsigned))
        or value["schema"] != "tgw-nix-observer-render-evaluation-receipt/v1"
        or value["request_sha256"] != request["request_sha256"]
        or value["outcome"] != "VERIFIED"
        or value["metadata_status"] != "NON_DEPLOYABLE_RENDER_FIXTURE"
        or value["cleanup"] != "removed"
        or value["effects"] != _a2_effects(True)
        or not isinstance(files, list)
        or [item.get("path") for item in files if isinstance(item, dict)] != list(OUTPUTS)
        or any(
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "size"}
            or not isinstance(item["sha256"], str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["sha256"])
            or type(item["size"]) is not int
            or item["size"] < 0
            for item in files
        )
        or value["output_manifest_sha256"] != _sha256(canonical(files))
        or not isinstance(value["output_root"], str)
        or not _store_path(value["output_root"], store_root)
        or not isinstance(value["evaluated_drv"], str)
        or not _store_path(value["evaluated_drv"], Path(authority.derivation_store_root), derivation=True)
        or value["drv_output"] != {"drv": value["evaluated_drv"], "output": value["output_root"]}
    ):
        raise RenderHelperError("provider receipt identity is invalid", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    verify_fields = {
        "executable_sha256",
        "version",
        "argv",
        "exit_code",
        "stdout_bytes",
        "stdout_sha256",
        "stderr_bytes",
        "stderr_sha256",
        "units_sha256",
        "observed_at",
        "host_identity_receipt_sha256",
    }
    units = files[9:12]
    if not isinstance(verify, dict) or set(verify) != verify_fields:
        raise RenderHelperError("provider verifier evidence is not closed", stage="request", diagnostic_code="VALIDATION_REFUSED")
    try:
        observed = datetime.strptime(verify.get("observed_at", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise RenderHelperError("provider verifier timestamp is invalid", stage="request", diagnostic_code="VALIDATION_REFUSED") from exc
    current = datetime.now(timezone.utc)
    if (
        verify["executable_sha256"] != request["systemd_analyze_sha256"]
        or verify["version"] != request["systemd_analyze_version"]
        or verify["argv"] != ["systemd-analyze", "verify", *OUTPUTS[9:12]]
        or verify["exit_code"] != 0
        or verify["host_identity_receipt_sha256"] != authority.a2_prerequisite_receipt_sha256
        or not isinstance(verify["observed_at"], str)
        or any(type(verify[key]) is not int or not 0 <= verify[key] <= request["max_output_bytes"] for key in ("stdout_bytes", "stderr_bytes"))
        or any(not isinstance(verify[key], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", verify[key]) for key in ("stdout_sha256", "stderr_sha256", "units_sha256"))
        or verify["units_sha256"] != _sha256(canonical(units))
        or observed > current.replace(microsecond=0) + timedelta(minutes=1)
        or current - observed > timedelta(hours=1)
    ):
        raise RenderHelperError("provider verifier evidence is invalid", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    # Reuse the exact provider's complete validator instead of maintaining a
    # permissive envelope-only approximation.  The source bytes must match the
    # request before they can supply schema or freshness semantics.
    try:
        provider_path = Path(__file__).with_name("nix_observer_render_evaluation.py")
        provider_raw = provider_path.read_bytes()
        if _sha256(provider_raw) != request["provider_sha256"]:
            raise RenderHelperError("provider validator source identity mismatch", stage="request", diagnostic_code="IDENTITY_MISMATCH")
        namespace: dict[str, Any] = {"__name__": "tgw.nix_observer_render_evaluation_envelope_validator"}
        exec(compile(provider_raw, "<bound-nix-observer-render-evaluation-validator>", "exec"), namespace)
        namespace["STORE_ROOT"] = Path(authority.store_root)
        validated = namespace["validate_result"](value, request=request, now=datetime.now(timezone.utc))
    except RenderHelperError:
        raise
    except BaseException as exc:
        raise RenderHelperError("provider receipt failed its bound validator", stage="request", diagnostic_code="VALIDATION_REFUSED") from exc
    return dict(validated)


def validate_a2_terminal(
    value: Any,
    *,
    binding: WireBinding,
    request: Mapping[str, Any],
    tool_descriptor: Mapping[str, Any],
    authority: A2Authority = PRODUCTION_A2_AUTHORITY,
) -> dict[str, Any]:
    validated = _validate_request(dict(request))
    tool = _validate_tool_descriptor(dict(tool_descriptor), request_sha256=validated["request_sha256"])
    if not isinstance(value, dict) or len(canonical(value)) > validated["max_output_bytes"]:
        raise RenderHelperError("A2 terminal is absent or oversized", stage="request", diagnostic_code="VALIDATION_REFUSED")
    if tool["authority_receipt_sha256"] != authority.a1_prerequisite_receipt_sha256:
        raise RenderHelperError("A2 prerequisite composition is invalid", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    input_entry = validated["input_closure_manifest"][0]
    if (
        validated["host_identity_receipt_sha256"] != authority.a2_prerequisite_receipt_sha256
        or validated["systemd_analyze_sha256"] != authority.systemd_analyze_sha256
        or validated["systemd_analyze_version"] != authority.systemd_analyze_version
        or validated["systemd_analyze_version_stdout_sha256"] != authority.systemd_analyze_version_stdout_sha256
        or validated["systemd_analyze_version_stdout_bytes"] != authority.systemd_analyze_version_stdout_bytes
        or input_entry["store_path"] != authority.input_path
        or input_entry["nar_sha256"] != authority.input_nar_sha256
    ):
        raise RenderHelperError("A2 request authority composition is invalid", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    execution_policy = _validate_execution_policy(value.get("execution_policy"), authority=authority)
    expected_base = _a2_terminal_base(
        binding,
        validated,
        tool_manifest_sha256=_sha256(canonical(_a2_tool_manifest(authority))),
        effect_sha256=_sha256(canonical(RENDER_EFFECT)),
        execution_policy=execution_policy,
        authority=authority,
    )
    if any(value.get(key) != expected for key, expected in expected_base.items()):
        raise RenderHelperError("A2 terminal binding is invalid", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    unsigned = dict(value)
    claimed = unsigned.pop("receipt_sha256", None)
    if claimed != _sha256(canonical(unsigned)):
        raise RenderHelperError("A2 terminal self-hash is invalid", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    common = set(expected_base) | {"schema", "outcome", "cleanup", "effects", "receipt_sha256"}
    if value.get("schema") == SUCCESS_SCHEMA:
        fields = common | {
            "provider_receipt_sha256",
            "closure_manifest",
            "closure_manifest_sha256",
            "closure_path_count",
            "provider_receipt",
        }
        provider = _validate_provider_receipt(value.get("provider_receipt"), request=validated, authority=authority)
        closure = value.get("closure_manifest")
        if (
            set(value) != fields
            or value["outcome"] != "VERIFIED"
            or value["cleanup"] != "removed"
            or value["effects"] != _a2_effects(True)
            or value["provider_receipt_sha256"] != provider["receipt_sha256"]
            or not isinstance(closure, list)
            or not 1 <= len(closure) <= 10_000
            or closure != sorted(closure, key=lambda item: item.get("path", "") if isinstance(item, dict) else "")
            or any(
                not isinstance(item, dict)
                or set(item) != {"path", "nar_sha256"}
                or not isinstance(item["path"], str)
                or not _store_path(item["path"], Path(authority.store_root))
                or not isinstance(item["nar_sha256"], str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["nar_sha256"])
                for item in closure
            )
            or len({item["path"] for item in closure}) != len(closure)
            or provider["output_root"] not in {item["path"] for item in closure}
            or value["closure_path_count"] != len(closure)
            or value["closure_manifest_sha256"] != _sha256(canonical(closure))
        ):
            raise RenderHelperError("A2 success envelope is invalid", stage="request", diagnostic_code="VALIDATION_REFUSED")
    elif value.get("schema") == A2_FAILURE_SCHEMA:
        fields = common | {
            "stage",
            "diagnostic_code",
            "original_stage",
            "original_diagnostic_code",
            "subprocess_step",
            "return_code",
        }
        if set(value) != fields:
            raise RenderHelperError("A2 failure envelope is not closed", stage="request", diagnostic_code="VALIDATION_REFUSED")
        valid_failures = {
            "a2-tool": {"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "BOUND_EXCEEDED"},
            "a2-input": {"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "BOUND_EXCEEDED", "SUBPROCESS_FAILED"},
            "a2-eval": {"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "BOUND_EXCEEDED", "SUBPROCESS_FAILED"},
            "a2-build": {"IDENTITY_MISMATCH", "BOUND_EXCEEDED", "SUBPROCESS_FAILED"},
            "a2-closure": {"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "BOUND_EXCEEDED", "SUBPROCESS_FAILED"},
            "provider": {"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "BOUND_EXCEEDED", "SUBPROCESS_FAILED"},
            "a2-verified": {"IDENTITY_MISMATCH", "INTERNAL_ERROR", "NONE"},
            "internal": {"INTERNAL_ERROR"},
        }
        valid_steps = {
            "a2-input": {"flake-metadata", "input-resolve", "input-hash", "input-references"},
            "a2-eval": {"drv-eval", "drv-show"},
            "a2-build": {"drv-build"},
            "a2-closure": {"closure-requisites", "closure-hash"},
            "provider": {"provider-drv-show", "provider-verifier-version", "provider-systemd-verify"},
        }
        if value["outcome"] == "AMBIGUOUS":
            lifecycle = (
                value["stage"] == "cleanup"
                and value["diagnostic_code"] == "CLEANUP_FAILED"
                and value["cleanup"] == "failed"
                and value["original_stage"] in valid_failures
                and value["original_diagnostic_code"] in valid_failures[value["original_stage"]]
            )
        else:
            lifecycle = (
                value["outcome"] == "FAILED"
                and value["stage"] == value["original_stage"]
                and value["diagnostic_code"] == value["original_diagnostic_code"]
                and value["cleanup"] == "removed"
                and value["original_stage"] in valid_failures
                and value["original_diagnostic_code"] in valid_failures[value["original_stage"]] - {"NONE"}
            )
        expected_build = value["original_stage"] in {"a2-build", "a2-closure", "provider", "a2-verified"}
        if value["original_diagnostic_code"] == "SUBPROCESS_FAILED":
            process_evidence = (
                value["original_stage"] in valid_steps
                and value["subprocess_step"] in valid_steps[value["original_stage"]]
                and (value["return_code"] is None or type(value["return_code"]) is int and -255 <= value["return_code"] <= 255)
                and value["return_code"] != 0
            )
        elif value["original_diagnostic_code"] == "BOUND_EXCEEDED":
            if value["original_stage"] == "a2-tool":
                process_evidence = value["subprocess_step"] is None and value["return_code"] is None
            else:
                process_evidence = (
                    value["original_stage"] in valid_steps
                    and value["subprocess_step"] in valid_steps[value["original_stage"]]
                    and value["return_code"] is None
                )
        elif value["original_diagnostic_code"] == "VALIDATION_REFUSED" and value["subprocess_step"] is not None:
            process_evidence = (
                value["original_stage"] in valid_steps
                and value["subprocess_step"] in valid_steps[value["original_stage"]]
                and type(value["return_code"]) is int
                and -255 <= value["return_code"] <= 255
            )
        else:
            process_evidence = value["subprocess_step"] is None and value["return_code"] is None
        if (
            not lifecycle
            or not process_evidence
            or value["effects"] != _a2_effects(expected_build)
        ):
            raise RenderHelperError("A2 failure envelope is invalid", stage="request", diagnostic_code="VALIDATION_REFUSED")
    else:
        raise RenderHelperError("A2 terminal schema is unknown", stage="request", diagnostic_code="VALIDATION_REFUSED")
    return dict(value)


def _binding_from_globals() -> WireBinding:
    names = {
        "request_bytes": "_BOOTSTRAP_REQUEST_BYTES",
        "helper_bytes": "_BOOTSTRAP_HELPER_BYTES",
        "tool_descriptor_bytes": "_BOOTSTRAP_TOOL_BYTES",
        "archive_bytes": "_BOOTSTRAP_ARCHIVE_BYTES",
        "request_sha256": "_BOOTSTRAP_REQUEST_SHA256",
        "helper_sha256": "_BOOTSTRAP_HELPER_SHA256",
        "tool_descriptor_sha256": "_BOOTSTRAP_TOOL_SHA256",
        "archive_sha256": "_BOOTSTRAP_ARCHIVE_SHA256",
    }
    values = {field: globals().get(name) for field, name in names.items()}
    if any(value is None for value in values.values()):
        raise RenderHelperError("bootstrap binding is absent", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    return WireBinding(**values)


def _open_bound_tool(tool: Mapping[str, Any], binding: WireBinding, *, authority: ToolAuthority) -> HeldTool:
    raw_descriptor = canonical(dict(tool))
    if len(raw_descriptor) != binding.tool_descriptor_bytes or _sha256(raw_descriptor) != binding.tool_descriptor_sha256:
        raise RenderHelperError("source tool descriptor binding mismatch", stage="tool", diagnostic_code="IDENTITY_MISMATCH")
    expected = _expected_tool_descriptor(binding.request_sha256, authority)
    if dict(tool) != expected:
        raise RenderHelperError("source tool is not authorized by the fixed host receipt", stage="tool", diagnostic_code="IDENTITY_MISMATCH")
    held = _open_resolved_regular(Path(tool["path"]), authority=authority)
    try:
        raw, identity = _read_held(held.descriptor, stage="tool", max_bytes=64 * 1024 * 1024)
        if identity.size != tool["bytes"] or _sha256(raw) != tool["sha256"]:
            raise RenderHelperError("fixed Git executable identity mismatch", stage="tool", diagnostic_code="IDENTITY_MISMATCH")
        return held
    except BaseException:
        _close_held_tool(held)
        raise


def _assert_bound_tool_unchanged(held: HeldTool, tool: Mapping[str, Any]) -> None:
    observed, identity = _read_held(held.descriptor, stage="tool", max_bytes=64 * 1024 * 1024)
    current_tool_identity = _tool_path_identity(os.fstat(held.descriptor))
    current_components = tuple(_tool_path_identity(os.fstat(fd)) for fd in held.component_descriptors)
    if (
        current_tool_identity != held.identity
        or current_components != held.component_identities
        or identity.size != tool["bytes"]
        or _sha256(observed) != tool["sha256"]
    ):
        raise RenderHelperError("held Git executable changed during use", stage="tool", diagnostic_code="IDENTITY_MISMATCH")


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


def _a2_tool_authority(path: str, digest: str, authority: A2Authority) -> ToolAuthority:
    return ToolAuthority(
        authority_receipt_sha256=authority.a1_prerequisite_receipt_sha256,
        path=path,
        sha256=digest,
        bytes=1,
        owner_uid=authority.owner_uid,
        mode=authority.mode,
        require_nix_store=authority.require_nix_store,
        forbid_owner_write=authority.require_nix_store,
        allow_mutable_parents=authority.allow_mutable_parents,
    )


def _open_a2_executable(name: str, path: str, digest: str, authority: A2Authority) -> tuple[HeldTool, dict[str, Any]]:
    tool_authority = _a2_tool_authority(path, digest, authority)
    try:
        held = _open_resolved_regular(Path(path), authority=tool_authority)
    except BaseException as exc:
        code = exc.diagnostic_code if isinstance(exc, RenderHelperError) else "IDENTITY_MISMATCH"
        raise RenderHelperError("Phase A2 executable is unavailable", stage="a2-tool", diagnostic_code=code) from exc
    try:
        raw, identity = _read_held(held.descriptor, stage="a2-tool", max_bytes=64 * 1024 * 1024)
        if _sha256(raw) != digest:
            raise RenderHelperError("Phase A2 executable digest mismatch", stage="a2-tool", diagnostic_code="IDENTITY_MISMATCH")
        manifest: dict[str, Any] = {
            "name": name,
            "path": path,
            "sha256": digest,
            "owner_uid": authority.owner_uid,
            "mode": f"{authority.mode:04o}",
        }
        if name == "systemd-analyze":
            manifest.update(
                version=authority.systemd_analyze_version,
                version_stdout_sha256=authority.systemd_analyze_version_stdout_sha256,
                version_stdout_bytes=authority.systemd_analyze_version_stdout_bytes,
            )
        return held, manifest
    except BaseException:
        _close_held_tool(held)
        raise


def _assert_a2_tool_unchanged(held: HeldTool, manifest: Mapping[str, Any], *, allow_mutable_parents: bool = False) -> None:
    raw, identity = _read_held(held.descriptor, stage="a2-tool", max_bytes=64 * 1024 * 1024)
    current_tool_identity = _tool_path_identity(os.fstat(held.descriptor))
    current_components = tuple(_tool_path_identity(os.fstat(fd)) for fd in held.component_descriptors)
    if (
        current_tool_identity != held.identity
        or (not allow_mutable_parents and current_components != held.component_identities)
        or _sha256(raw) != manifest["sha256"]
    ):
        raise RenderHelperError("Phase A2 executable changed while held", stage="a2-tool", diagnostic_code="IDENTITY_MISMATCH")


def _fixed_nix_environment(run_path: Path) -> dict[str, str]:
    home = run_path / "nix-home"
    temporary = run_path / "tmp"
    for directory, label in ((home, "HOME"), (temporary, "TMPDIR")):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            metadata = directory.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise RenderHelperError(f"Phase A2 {label} trust mismatch", stage="a2-tool", diagnostic_code="IDENTITY_MISMATCH")
    return {
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "LC_ALL": "C",
        "LANG": "C",
        "PATH": "/no-ambient-path",
        "NIX_REMOTE": "local",
        "NIX_CONFIG": NIX_CONFIG,
    }


def _execution_policy(run_path: Path) -> dict[str, Any]:
    environment = _fixed_nix_environment(run_path)
    return {
        "schema": "tgw-nix-observer-render-execution-policy/v1",
        "environment": environment,
        "nix_argv_prefix": list(NIX_ARGV_PREFIX),
        "render_attr": RENDER_ATTR,
        "build_selector": "evaluated-drv^out",
        "ambient_environment_inherited": False,
        "remote_builders": False,
        "builder_substitutes": False,
        "sandbox_required": True,
        "sandbox_fallback": False,
    }


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()
    process.wait()


def _run_bounded(
    argv: list[str],
    *,
    executable: str,
    cwd: Path,
    env: Mapping[str, str],
    pass_fds: tuple[int, ...],
    timeout: float,
    max_output_bytes: int,
    stage: str,
    subprocess_step: str,
    text: bool = False,
) -> subprocess.CompletedProcess[Any]:
    """Run one held executable with a hard wall-time and combined-output cap."""
    if timeout <= 0:
        raise RenderHelperError(
            "Phase A2 duration exhausted",
            stage=stage,
            diagnostic_code="BOUND_EXCEEDED",
            subprocess_step=subprocess_step,
        )
    try:
        process = subprocess.Popen(
            argv,
            executable=executable,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=True,
        )
    except OSError as exc:
        raise RenderHelperError(
            "Phase A2 subprocess could not start",
            stage=stage,
            diagnostic_code="SUBPROCESS_FAILED",
            subprocess_step=subprocess_step,
        ) from exc
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()
    streams = {stdout_fd: bytearray(), stderr_fd: bytearray()}
    selector.register(process.stdout, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(process)
                raise RenderHelperError(
                    "Phase A2 subprocess timed out",
                    stage=stage,
                    diagnostic_code="BOUND_EXCEEDED",
                    subprocess_step=subprocess_step,
                )
            for key, _ in selector.select(min(remaining, 0.25)):
                block = os.read(key.fd, min(64 * 1024, max_output_bytes + 1))
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                streams[key.fd].extend(block)
                if sum(len(value) for value in streams.values()) > max_output_bytes:
                    _terminate_process(process)
                    raise RenderHelperError(
                        "Phase A2 subprocess output exceeded bound",
                        stage=stage,
                        diagnostic_code="BOUND_EXCEEDED",
                        subprocess_step=subprocess_step,
                    )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process(process)
            raise RenderHelperError(
                "Phase A2 subprocess timed out",
                stage=stage,
                diagnostic_code="BOUND_EXCEEDED",
                subprocess_step=subprocess_step,
            )
        return_code = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process)
        raise RenderHelperError(
            "Phase A2 subprocess timed out",
            stage=stage,
            diagnostic_code="BOUND_EXCEEDED",
            subprocess_step=subprocess_step,
        ) from exc
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    stdout = bytes(streams[stdout_fd])
    stderr = bytes(streams[stderr_fd])
    if text:
        try:
            return subprocess.CompletedProcess(argv, return_code, stdout.decode(), stderr.decode())
        except UnicodeDecodeError as exc:
            raise RenderHelperError(
                "Phase A2 subprocess output is not UTF-8",
                stage=stage,
                diagnostic_code="VALIDATION_REFUSED",
                subprocess_step=subprocess_step,
                return_code=return_code,
            ) from exc
    return subprocess.CompletedProcess(argv, return_code, stdout, stderr)


def _remaining(deadline: float, requested: float) -> float:
    return min(requested, max(0.0, deadline - time.monotonic()))


def _run_a2(
    held: HeldTool,
    label: str,
    arguments: list[str],
    *,
    source: Path,
    env: Mapping[str, str],
    deadline: float,
    request: Mapping[str, Any],
    stage: str,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    executable = f"/proc/self/fd/{held.descriptor}"
    if stage == "a2-input":
        step = (
            "flake-metadata"
            if "metadata" in arguments
            else "input-resolve"
            if "eval" in arguments
            else "input-hash"
            if "hash" in arguments
            else "input-references"
        )
    elif stage == "a2-eval":
        step = "drv-show" if "derivation" in arguments else "drv-eval"
    elif stage == "a2-build":
        step = "drv-build"
    elif stage == "a2-closure":
        step = "closure-hash" if "hash" in arguments else "closure-requisites"
    else:
        step = stage
    result = _run_bounded(
        [label, *arguments],
        executable=executable,
        cwd=source,
        env=env,
        pass_fds=(held.descriptor,),
        timeout=_remaining(deadline, request["max_duration_seconds"]),
        max_output_bytes=request["max_output_bytes"],
        stage=stage,
        subprocess_step=step,
        text=text,
    )
    result.tgw_subprocess_step = step
    return result


def _require_success(result: subprocess.CompletedProcess[Any], *, stage: str, subprocess_step: str | None = None) -> str:
    if result.returncode != 0:
        raise RenderHelperError(
            "Phase A2 subprocess failed",
            stage=stage,
            diagnostic_code="SUBPROCESS_FAILED",
            subprocess_step=subprocess_step or getattr(result, "tgw_subprocess_step", stage),
            return_code=result.returncode,
        )
    if not isinstance(result.stdout, str):
        raise RenderHelperError("Phase A2 subprocess result type mismatch", stage=stage, diagnostic_code="VALIDATION_REFUSED")
    return result.stdout


def _store_path(value: str, root: Path, *, derivation: bool = False) -> bool:
    suffix = r"\.drv" if derivation else ""
    return bool(re.fullmatch(re.escape(str(root)) + r"/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+" + suffix, value))


def _a2_effects(build_attempted: bool) -> dict[str, bool]:
    return {
        "build": build_attempted,
        "activation": False,
        "deployment": False,
        "profile_write": False,
        "home_db_write": False,
        "live_flake_write": False,
        "network": False,
    }


def _a2_terminal_base(
    binding: WireBinding,
    request: Mapping[str, Any],
    *,
    tool_manifest_sha256: str,
    effect_sha256: str,
    execution_policy: Mapping[str, Any],
    authority: A2Authority,
) -> dict[str, Any]:
    return {
        "request_sha256": binding.request_sha256,
        "effect_sha256": effect_sha256,
        "helper_sha256": binding.helper_sha256,
        "provider_source_sha256": request["provider_sha256"],
        "a1_prerequisite_receipt_sha256": authority.a1_prerequisite_receipt_sha256,
        "a2_prerequisite_receipt_sha256": authority.a2_prerequisite_receipt_sha256,
        "tool_manifest_sha256": tool_manifest_sha256,
        "input_manifest_sha256": request["input_closure_manifest_sha256"],
        "execution_policy": dict(execution_policy),
        "execution_policy_sha256": _sha256(canonical(execution_policy)),
        "archive_sha256": binding.archive_sha256,
        "source_commit": request["source_commit"],
        "source_tree": request["source_tree"],
    }


def _a2_failure(
    binding: WireBinding,
    request: Mapping[str, Any],
    *,
    original_stage: str,
    original_code: str,
    cleanup: str,
    build_attempted: bool,
    tool_manifest_sha256: str,
    execution_policy: Mapping[str, Any],
    subprocess_step: str | None,
    return_code: int | None,
    authority: A2Authority,
) -> dict[str, Any]:
    ambiguous = cleanup == "failed"
    value = {
        "schema": A2_FAILURE_SCHEMA,
        "outcome": "AMBIGUOUS" if ambiguous else "FAILED",
        "stage": "cleanup" if ambiguous else original_stage,
        "diagnostic_code": "CLEANUP_FAILED" if ambiguous else original_code,
        "original_stage": original_stage,
        "original_diagnostic_code": original_code,
        "subprocess_step": subprocess_step,
        "return_code": return_code,
        "cleanup": cleanup,
        "effects": _a2_effects(build_attempted),
        **_a2_terminal_base(
            binding,
            request,
            tool_manifest_sha256=tool_manifest_sha256,
            effect_sha256=_sha256(canonical(RENDER_EFFECT)),
            execution_policy=execution_policy,
            authority=authority,
        ),
    }
    value["receipt_sha256"] = _sha256(canonical(value))
    return value


def _execute_a2_inner(
    source: Path,
    request: Mapping[str, Any],
    binding: WireBinding,
    run_path: Path,
    *,
    authority: A2Authority,
    held_tools: list[HeldTool],
    git_fd: int,
    effect_state: dict[str, bool],
) -> tuple[dict[str, Any], str]:
    if (
        authority.a1_prerequisite_receipt_sha256 != AUTHORIZED_TOOL_RECEIPT_SHA256
        and authority.require_nix_store
    ) or request["host_identity_receipt_sha256"] != authority.a2_prerequisite_receipt_sha256:
        raise RenderHelperError("Phase A2 prerequisite receipt mismatch", stage="a2-tool", diagnostic_code="IDENTITY_MISMATCH")
    input_entry = request["input_closure_manifest"][0]
    if input_entry["store_path"] != authority.input_path or input_entry["nar_sha256"] != authority.input_nar_sha256:
        raise RenderHelperError("Phase A2 input authority mismatch", stage="a2-input", diagnostic_code="IDENTITY_MISMATCH")
    if request["systemd_analyze_sha256"] != authority.systemd_analyze_sha256:
        raise RenderHelperError("Phase A2 verifier authority mismatch", stage="a2-tool", diagnostic_code="IDENTITY_MISMATCH")

    manifests: list[dict[str, Any]] = []
    for name, path, digest in (
        ("nix", authority.nix_path, authority.nix_sha256),
        ("nix-store", authority.nix_store_path, authority.nix_store_sha256),
        ("systemd-analyze", authority.systemd_analyze_path, authority.systemd_analyze_sha256),
    ):
        held, manifest = _open_a2_executable(name, path, digest, authority)
        held_tools.append(held)
        manifests.append(manifest)
    tool_manifest = {
        "schema": "tgw-nix-observer-render-a2-tools/v1",
        "a1_prerequisite_receipt_sha256": authority.a1_prerequisite_receipt_sha256,
        "a2_prerequisite_receipt_sha256": authority.a2_prerequisite_receipt_sha256,
        "tools": manifests,
    }
    tool_manifest_sha256 = _sha256(canonical(tool_manifest))
    nix, nix_store, systemd_analyze = held_tools
    env = _fixed_nix_environment(run_path)
    execution_policy = _execution_policy(run_path)
    deadline = time.monotonic() + request["max_duration_seconds"]
    nix_base = list(NIX_ARGV_PREFIX)

    metadata_raw = _require_success(
        _run_a2(nix, "nix", [*nix_base, "flake", "metadata", "--json", "path:."], source=source, env=env, deadline=deadline, request=request, stage="a2-input"),
        stage="a2-input",
    )
    try:
        metadata = json.loads(metadata_raw)
        nodes = metadata["locks"]["nodes"]
        locked = nodes["nixpkgs"]["locked"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RenderHelperError("Phase A2 flake metadata is invalid", stage="a2-input", diagnostic_code="VALIDATION_REFUSED") from exc
    if set(nodes) != {"root", "nixpkgs"} or locked.get("rev") != input_entry["rev"] or locked.get("narHash") != input_entry["lock_nar_hash"]:
        raise RenderHelperError("Phase A2 lock graph mismatch", stage="a2-input", diagnostic_code="IDENTITY_MISMATCH")
    input_path = _require_success(
        _run_a2(nix, "nix", [*nix_base, "eval", "--raw", "path:.#inputIdentities.nixpkgs.outPath"], source=source, env=env, deadline=deadline, request=request, stage="a2-input"),
        stage="a2-input",
    ).strip()
    if input_path != authority.input_path:
        raise RenderHelperError("Phase A2 resolved input path mismatch", stage="a2-input", diagnostic_code="IDENTITY_MISMATCH")
    held_input_path = authority.held_input_path or input_path
    input_fd = os.open(held_input_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    input_identity = _tool_path_identity(os.fstat(input_fd))
    try:
        input_nar = _require_success(
            _run_a2(nix, "nix", [*nix_base, "hash", "path", "--type", "sha256", "--base16", input_path], source=source, env=env, deadline=deadline, request=request, stage="a2-input"),
            stage="a2-input",
        ).strip()
        if "sha256:" + input_nar != authority.input_nar_sha256:
            raise RenderHelperError("Phase A2 input NAR mismatch", stage="a2-input", diagnostic_code="IDENTITY_MISMATCH")
        references = _require_success(
            _run_a2(nix_store, "nix-store", ["--query", "--references", input_path], source=source, env=env, deadline=deadline, request=request, stage="a2-input"),
            stage="a2-input",
        )
        if references.strip() or _tool_path_identity(os.fstat(input_fd)) != input_identity:
            raise RenderHelperError("Phase A2 input closure is not exact and held", stage="a2-input", diagnostic_code="IDENTITY_MISMATCH")
    finally:
        os.close(input_fd)

    target = "path:.#" + RENDER_ATTR
    drv = _require_success(
        _run_a2(nix, "nix", [*nix_base, "eval", "--raw", target + ".drvPath"], source=source, env=env, deadline=deadline, request=request, stage="a2-eval"),
        stage="a2-eval",
    ).strip()
    store_root = Path(authority.store_root)
    if not _store_path(drv, Path(authority.derivation_store_root), derivation=True):
        raise RenderHelperError("Phase A2 derivation is not an exact singleton", stage="a2-eval", diagnostic_code="VALIDATION_REFUSED")
    derivation_raw = _require_success(
        _run_a2(nix, "nix", [*nix_base, "derivation", "show", drv], source=source, env=env, deadline=deadline, request=request, stage="a2-eval"),
        stage="a2-eval",
    )
    try:
        derivation = json.loads(derivation_raw)
        outputs = derivation[drv]["outputs"]
        output_path = outputs["out"]["path"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RenderHelperError("Phase A2 derivation schema is invalid", stage="a2-eval", diagnostic_code="VALIDATION_REFUSED") from exc
    if set(derivation) != {drv} or outputs != {"out": {"path": output_path}} or not _store_path(output_path, store_root):
        raise RenderHelperError("Phase A2 derivation output is not exact", stage="a2-eval", diagnostic_code="IDENTITY_MISMATCH")
    effect_state["build_attempted"] = True
    built = _require_success(
        _run_a2(nix, "nix", [*nix_base, "build", "--no-link", "--print-out-paths", drv + "^out"], source=source, env=env, deadline=deadline, request=request, stage="a2-build"),
        stage="a2-build",
    ).strip()
    if built != output_path:
        raise RenderHelperError("Phase A2 build output is not the evaluated singleton", stage="a2-build", diagnostic_code="IDENTITY_MISMATCH")

    requisites_raw = _require_success(
        _run_a2(nix_store, "nix-store", ["--query", "--requisites", output_path], source=source, env=env, deadline=deadline, request=request, stage="a2-closure"),
        stage="a2-closure",
    )
    requisites = sorted(set(requisites_raw.splitlines()))
    if not requisites or len(requisites) > 10_000 or output_path not in requisites or any(not _store_path(item, store_root) for item in requisites):
        raise RenderHelperError("Phase A2 closure is incomplete", stage="a2-closure", diagnostic_code="VALIDATION_REFUSED")
    closure_manifest = []
    for item in requisites:
        nar = _require_success(
            _run_a2(nix, "nix", [*nix_base, "hash", "path", "--type", "sha256", "--base16", item], source=source, env=env, deadline=deadline, request=request, stage="a2-closure"),
            stage="a2-closure",
        ).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", nar):
            raise RenderHelperError("Phase A2 closure NAR is invalid", stage="a2-closure", diagnostic_code="VALIDATION_REFUSED")
        closure_manifest.append({"path": item, "nar_sha256": "sha256:" + nar})

    provider_path = source / SOURCE_DIGEST_PATHS["provider_sha256"]
    provider_fd = os.open(provider_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        provider_raw, provider_identity = _read_held(provider_fd, stage="provider", max_bytes=1024 * 1024)
        if _sha256(provider_raw) != request["provider_sha256"]:
            raise RenderHelperError("Phase A2 provider identity mismatch", stage="provider", diagnostic_code="IDENTITY_MISMATCH")
        namespace: dict[str, Any] = {"__name__": "tgw.nix_observer_render_evaluation_a2_bound"}
        exec(compile(provider_raw, "<bound-nix-observer-render-evaluation>", "exec"), namespace)
        provider_scratch = run_path / "provider-scratch"
        provider_scratch.mkdir(mode=0o700)
        namespace["STORE_ROOT"] = store_root
        namespace["SCRATCH_PARENT"] = provider_scratch
        provider_process: dict[str, Any] = {"step": None, "return_code": None}

        def provider_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            executable = argv[0]
            pass_fds = tuple(kwargs.get("pass_fds", ()))
            if not re.fullmatch(r"/proc/self/fd/[0-9]+", executable) or int(executable.rsplit("/", 1)[1]) not in pass_fds:
                raise RenderHelperError("provider requested an unheld executable", stage="provider", diagnostic_code="VALIDATION_REFUSED")
            if len(argv) > 1 and argv[1] == "verify":
                step = "provider-systemd-verify"
            elif len(argv) > 1 and argv[1] == "--version":
                step = "provider-verifier-version"
            else:
                step = "provider-drv-show"
            effective_argv = [argv[0], *NIX_ARGV_PREFIX, *argv[1:]] if step == "provider-drv-show" else argv
            result = _run_bounded(
                effective_argv,
                executable=executable,
                cwd=Path(kwargs.get("cwd", source)),
                env=env,
                pass_fds=pass_fds,
                timeout=_remaining(deadline, float(kwargs.get("timeout", 60))),
                max_output_bytes=request["max_output_bytes"],
                stage="provider",
                subprocess_step=step,
                text=bool(kwargs.get("text", False)),
            )
            provider_process.update(step=step, return_code=result.returncode)
            return result

        try:
            provider_receipt = namespace["produce_result"](
                request=request,
                output_root=Path(output_path),
                evaluated_drv=drv,
                nix=Path(authority.nix_path),
                nix_sha256=authority.nix_sha256,
                systemd_analyze=Path(authority.systemd_analyze_path),
                now=datetime.now(timezone.utc),
                run=provider_run,
            )
        except RenderHelperError:
            raise
        except BaseException as exc:
            return_code = provider_process["return_code"]
            diagnostic_code = "SUBPROCESS_FAILED" if isinstance(return_code, int) and return_code != 0 else "VALIDATION_REFUSED"
            raise RenderHelperError(
                "bound provider rejected the render",
                stage="provider",
                diagnostic_code=diagnostic_code,
                subprocess_step=provider_process["step"] if diagnostic_code == "SUBPROCESS_FAILED" else None,
                return_code=return_code if diagnostic_code == "SUBPROCESS_FAILED" else None,
            ) from exc
        if _identity(os.fstat(provider_fd)) != provider_identity:
            raise RenderHelperError("Phase A2 provider changed while held", stage="provider", diagnostic_code="IDENTITY_MISMATCH")
    finally:
        os.close(provider_fd)
    try:
        for held, manifest in zip(held_tools, manifests, strict=True):
            _assert_a2_tool_unchanged(held, manifest, allow_mutable_parents=authority.allow_mutable_parents)
        # `--no-write-lock-file` is enforced at the CLI, and this final complete
        # tree reconstruction proves that neither Nix nor the provider mutated
        # any admitted source byte while producing the evidence.
        _verify_source(source, request=request, git_fd=git_fd)
    except BaseException as exc:
        raise RenderHelperError("Phase A2 final identity check failed", stage="a2-verified", diagnostic_code="IDENTITY_MISMATCH") from exc
    success = {
        "schema": SUCCESS_SCHEMA,
        "outcome": "VERIFIED",
        "provider_receipt_sha256": provider_receipt["receipt_sha256"],
        "closure_manifest": closure_manifest,
        "closure_manifest_sha256": _sha256(canonical(closure_manifest)),
        "closure_path_count": len(closure_manifest),
        "cleanup": "removed",
        "effects": _a2_effects(True),
        "provider_receipt": provider_receipt,
        **_a2_terminal_base(
            binding,
            request,
            tool_manifest_sha256=tool_manifest_sha256,
            effect_sha256=_sha256(canonical(RENDER_EFFECT)),
            execution_policy=execution_policy,
            authority=authority,
        ),
    }
    success["receipt_sha256"] = _sha256(canonical(success))
    return success, tool_manifest_sha256


def _a2_tool_manifest(authority: A2Authority) -> dict[str, Any]:
    tools = []
    for name, path, digest in (
        ("nix", authority.nix_path, authority.nix_sha256),
        ("nix-store", authority.nix_store_path, authority.nix_store_sha256),
        ("systemd-analyze", authority.systemd_analyze_path, authority.systemd_analyze_sha256),
    ):
        entry: dict[str, Any] = {
            "name": name,
            "path": path,
            "sha256": digest,
            "owner_uid": authority.owner_uid,
            "mode": f"{authority.mode:04o}",
        }
        if name == "systemd-analyze":
            entry.update(
                version=authority.systemd_analyze_version,
                version_stdout_sha256=authority.systemd_analyze_version_stdout_sha256,
                version_stdout_bytes=authority.systemd_analyze_version_stdout_bytes,
            )
        tools.append(entry)
    return {
        "schema": "tgw-nix-observer-render-a2-tools/v1",
        "a1_prerequisite_receipt_sha256": authority.a1_prerequisite_receipt_sha256,
        "a2_prerequisite_receipt_sha256": authority.a2_prerequisite_receipt_sha256,
        "tools": tools,
    }


def _validate_execution_policy(value: Any, *, authority: A2Authority) -> dict[str, Any]:
    fields = {
        "schema",
        "environment",
        "nix_argv_prefix",
        "render_attr",
        "build_selector",
        "ambient_environment_inherited",
        "remote_builders",
        "builder_substitutes",
        "sandbox_required",
        "sandbox_fallback",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RenderHelperError("A2 execution policy is not closed", stage="request", diagnostic_code="VALIDATION_REFUSED")
    environment = value["environment"]
    if not isinstance(environment, dict) or set(environment) != {"HOME", "TMPDIR", "LC_ALL", "LANG", "PATH", "NIX_REMOTE", "NIX_CONFIG"}:
        raise RenderHelperError("A2 execution environment is not closed", stage="request", diagnostic_code="VALIDATION_REFUSED")
    home = Path(environment["HOME"]) if isinstance(environment["HOME"], str) else Path()
    temporary = Path(environment["TMPDIR"]) if isinstance(environment["TMPDIR"], str) else Path()
    run_name = home.parent.name
    expected_environment = {
        "LC_ALL": "C",
        "LANG": "C",
        "PATH": "/no-ambient-path",
        "NIX_REMOTE": "local",
        "NIX_CONFIG": NIX_CONFIG,
    }
    if (
        home.name != "nix-home"
        or not re.fullmatch(r"run-[0-9a-f]{32}", run_name)
        or home.parent.parent != Path(authority.scratch_root)
        or temporary != home.parent / "tmp"
        or any(environment[key] != expected for key, expected in expected_environment.items())
        or value["schema"] != "tgw-nix-observer-render-execution-policy/v1"
        or value["nix_argv_prefix"] != list(NIX_ARGV_PREFIX)
        or value["render_attr"] != RENDER_ATTR
        or value["build_selector"] != "evaluated-drv^out"
        or value["ambient_environment_inherited"] is not False
        or value["remote_builders"] is not False
        or value["builder_substitutes"] is not False
        or value["sandbox_required"] is not True
        or value["sandbox_fallback"] is not False
    ):
        raise RenderHelperError("A2 execution policy is not local-only", stage="request", diagnostic_code="IDENTITY_MISMATCH")
    return dict(value)


def _execute_a2(
    source: Path,
    request: Mapping[str, Any],
    binding: WireBinding,
    run_path: Path,
    *,
    authority: A2Authority,
    git_fd: int,
    effect_state: dict[str, bool],
) -> tuple[dict[str, Any], str]:
    held_tools: list[HeldTool] = []
    try:
        return _execute_a2_inner(
            source,
            request,
            binding,
            run_path,
            authority=authority,
            held_tools=held_tools,
            git_fd=git_fd,
            effect_state=effect_state,
        )
    except RenderHelperError as exc:
        if effect_state["build_attempted"] and exc.stage not in {"a2-build", "a2-closure", "provider", "a2-verified"}:
            raise RenderHelperError("Phase A2 failed after build began", stage="a2-verified", diagnostic_code=exc.diagnostic_code) from exc
        raise
    except BaseException as exc:
        if effect_state["build_attempted"]:
            raise RenderHelperError("Phase A2 failed after build began", stage="a2-verified", diagnostic_code="INTERNAL_ERROR") from exc
        raise
    finally:
        for held in reversed(held_tools):
            _close_held_tool(held)


def _terminal_base(binding: WireBinding, request: Mapping[str, Any], tool: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "request_sha256": binding.request_sha256,
        "helper_sha256": binding.helper_sha256,
        "tool_descriptor_sha256": binding.tool_descriptor_sha256,
        "tool": dict(tool),
        "archive_sha256": binding.archive_sha256,
        "source_commit": request.get("source_commit", "unknown"),
        "source_tree": request.get("source_tree", "unknown"),
        "provider_sha256": request.get("provider_sha256", "unknown"),
        "effects": dict(EFFECTS),
    }


def _failure(
    binding: WireBinding,
    request: Mapping[str, Any],
    tool: Mapping[str, Any],
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
        **_terminal_base(binding, request, tool),
    }
    value["receipt_sha256"] = _sha256(canonical(value))
    return value


def _hold(binding: WireBinding, request: Mapping[str, Any], tool: Mapping[str, Any], verified: Mapping[str, str]) -> dict[str, Any]:
    value = {
        "schema": HOLD_SCHEMA,
        "outcome": HOLD_OUTCOME,
        "stage": "source-verified",
        "verified_source_files": dict(verified),
        "executor": {"installed": False, "reachable_from_request": False, "reachable_from_environment": False},
        "cleanup": "removed",
        **_terminal_base(binding, request, tool),
    }
    value["receipt_sha256"] = _sha256(canonical(value))
    return value


def execute_packet(
    stream: BinaryIO,
    *,
    binding: WireBinding | None = None,
    scratch_root: Path = SCRATCH_ROOT,
    scratch_uid: int | None = None,
    cleanup_tree: Callable[..., None] = shutil.rmtree,
    _test_executor: Callable[[Path, Mapping[str, Any]], str] | None = None,
    _test_tool_authority: ToolAuthority | None = None,
    _test_a2_authority: A2Authority | None = None,
    _production_tool_authority: ToolAuthority = PRODUCTION_GIT_AUTHORITY,
    _production_a2_authority: A2Authority = PRODUCTION_A2_AUTHORITY,
) -> dict[str, Any]:
    """Verify source, execute its one admitted render, then emit post-cleanup."""
    bound = binding or _binding_from_globals()
    authority = _production_tool_authority if _test_tool_authority is None else _test_tool_authority
    a2_authority = _production_a2_authority if _test_a2_authority is None else _test_a2_authority
    request: dict[str, Any] = {}
    tool: dict[str, Any] = {}
    parent_fd = root_fd = -1
    held_git: HeldTool | None = None
    root_created = False
    run_path: Path | None = None
    original_stage = "request"
    original_code = "INTERNAL_ERROR"
    verified: dict[str, str] | None = None
    marker: str | None = None
    a2_success: dict[str, Any] | None = None
    a2_started = False
    effect_state = {"build_attempted": False}
    a2_tool_manifest_sha256 = _sha256(canonical(_a2_tool_manifest(a2_authority)))
    a2_execution_policy: dict[str, Any] | None = None
    failure_subprocess_step: str | None = None
    failure_return_code: int | None = None
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
        tool_size_raw = stream.read(8)
        if len(tool_size_raw) != 8 or struct.unpack("!Q", tool_size_raw)[0] != bound.tool_descriptor_bytes:
            raise RenderHelperError("helper tool descriptor frame mismatch", stage="tool", diagnostic_code="IDENTITY_MISMATCH")
        tool_raw = stream.read(bound.tool_descriptor_bytes)
        if len(tool_raw) != bound.tool_descriptor_bytes or _sha256(tool_raw) != bound.tool_descriptor_sha256:
            raise RenderHelperError("helper tool descriptor is truncated or mismatched", stage="tool", diagnostic_code="IDENTITY_MISMATCH")
        try:
            tool = _validate_tool_descriptor(json.loads(tool_raw), request_sha256=bound.request_sha256)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RenderHelperError("helper tool descriptor JSON is invalid", stage="tool", diagnostic_code="VALIDATION_REFUSED") from exc
        if canonical(tool) != tool_raw:
            raise RenderHelperError("helper tool descriptor is not canonical", stage="tool", diagnostic_code="VALIDATION_REFUSED")
        _validate_tool_descriptor(tool, request_sha256=bound.request_sha256, authority=authority)
        request = _validate_request(request)
        if canonical(request) != request_raw or request["request_sha256"] != bound.request_sha256 or request["archive_sha256"] != bound.archive_sha256:
            raise RenderHelperError("helper request/bootstrap binding mismatch", stage="request", diagnostic_code="IDENTITY_MISMATCH")
        helper_hash = globals().get("_BOOTSTRAP_HELPER_SHA256", bound.helper_sha256)
        if helper_hash != bound.helper_sha256:
            raise RenderHelperError("helper/bootstrap source identity mismatch", stage="request", diagnostic_code="IDENTITY_MISMATCH")
        held_git = _open_bound_tool(tool, bound, authority=authority)
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
        verified = _verify_source(source, request=request, git_fd=held_git.descriptor)
        _assert_bound_tool_unchanged(held_git, tool)
        if _test_executor is not None:
            marker = _test_executor(source, request)
            if not isinstance(marker, str) or not 1 <= len(marker.encode()) <= 256:
                raise RenderHelperError("test executor marker is invalid", stage="test-executor", diagnostic_code="VALIDATION_REFUSED")
        else:
            a2_started = True
            a2_execution_policy = _execution_policy(run_path)
            if a2_authority.a1_prerequisite_receipt_sha256 != tool["authority_receipt_sha256"]:
                raise RenderHelperError("Phase A1/A2 prerequisite composition mismatch", stage="a2-tool", diagnostic_code="IDENTITY_MISMATCH")
            a2_success, observed_tool_manifest_sha256 = _execute_a2(
                source,
                request,
                bound,
                run_path,
                authority=a2_authority,
                git_fd=held_git.descriptor,
                effect_state=effect_state,
            )
            if observed_tool_manifest_sha256 != a2_tool_manifest_sha256:
                raise RenderHelperError("Phase A2 tool manifest changed", stage="a2-tool", diagnostic_code="IDENTITY_MISMATCH")
            _assert_bound_tool_unchanged(held_git, tool)
    except BaseException as exc:
        failure = exc
        if isinstance(exc, RenderHelperError):
            original_stage, original_code = exc.stage, exc.diagnostic_code
            failure_subprocess_step = exc.subprocess_step
            failure_return_code = exc.return_code
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
    elif a2_started:
        # A2 starts only after its run directory exists.  Its disappearance is
        # externally mutable cleanup evidence, never a truthful "not-created".
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
        if held_git is not None:
            _close_held_tool(held_git)
            held_git = None
        for descriptor in (root_fd, parent_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    if cleanup == "failed":
        if a2_started:
            assert a2_execution_policy is not None
            if failure is None:
                original_stage, original_code = "a2-verified", "NONE"
            return _a2_failure(
                bound,
                request,
                original_stage=original_stage,
                original_code=original_code,
                cleanup=cleanup,
                build_attempted=effect_state["build_attempted"],
                tool_manifest_sha256=a2_tool_manifest_sha256,
                execution_policy=a2_execution_policy,
                subprocess_step=failure_subprocess_step,
                return_code=failure_return_code,
                authority=a2_authority,
            )
        if failure is None:
            original_stage, original_code = "source-verified", "NONE"
        return _failure(bound, request, tool, original_stage=original_stage, original_code=original_code, cleanup=cleanup)
    if failure is not None:
        if a2_started:
            assert a2_execution_policy is not None
            return _a2_failure(
                bound,
                request,
                original_stage=original_stage,
                original_code=original_code,
                cleanup=cleanup,
                build_attempted=effect_state["build_attempted"],
                tool_manifest_sha256=a2_tool_manifest_sha256,
                execution_policy=a2_execution_policy,
                subprocess_step=failure_subprocess_step,
                return_code=failure_return_code,
                authority=a2_authority,
            )
        return _failure(bound, request, tool, original_stage=original_stage, original_code=original_code, cleanup=cleanup)
    if verified is None:
        return _failure(bound, request, tool, original_stage="internal", original_code="INTERNAL_ERROR", cleanup=cleanup)
    if marker is not None:
        value = {
            "schema": TEST_MARKER_SCHEMA,
            "outcome": "TEST_ONLY_SOURCE_VERIFIED",
            "marker": marker,
            "cleanup": "removed",
            **_terminal_base(bound, request, tool),
        }
        value["receipt_sha256"] = _sha256(canonical(value))
        return value
    if a2_success is None:
        return _failure(bound, request, tool, original_stage="internal", original_code="INTERNAL_ERROR", cleanup=cleanup)
    return a2_success


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
    if value["schema"] == SUCCESS_SCHEMA:
        return 0
    if value["outcome"] == HOLD_OUTCOME:
        return 3
    return 2 if value["outcome"] == "AMBIGUOUS" else 1


if __name__ == "__main__" and not globals().get("_BOOTSTRAP_DEFER_MAIN", False):
    raise SystemExit(main())
