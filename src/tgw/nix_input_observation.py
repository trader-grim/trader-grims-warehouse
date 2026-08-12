"""One-process, zero-egress observation of exact reviewed-evaluation inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Callable

SCHEMA = "tgw-nix-input-observation/v2"
MAGIC = b"TGWNIXO1"
PREFIX = struct.Struct("!8sIQQQ32s32s32s")
PYTHON = "/run/current-system/sw/bin/python3"
SUDO = "/run/current-system/sw/bin/sudo"
LAUNCHER = "/run/current-system/sw/bin/tgw-nix-input-observer-launcher"
UNSHARE = "/run/current-system/sw/bin/unshare"
IP = "/run/current-system/sw/bin/ip"
NIX = "/run/current-system/sw/bin/nix"
NIX_STORE = "/run/current-system/sw/bin/nix-store"
GIT = "/run/current-system/sw/bin/git"
TOOLS = {"sudo": SUDO, "launcher": LAUNCHER, "unshare": UNSHARE, "ip": IP, "python": PYTHON, "nix": NIX, "nix_store": NIX_STORE, "git": GIT}
NODE = "nixpkgs"
REV = "ac62194c3917d5f474c1a844b6fd6da2db95077d"
LOCK_NAR = "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w="
STORE_PATH = re.compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+(?:\.drv)?")
BOOTSTRAP = """import hashlib,io,json,struct,sys
P=struct.Struct('!8sIQQQ32s32s32s'); raw=sys.stdin.buffer.read(P.size)
def fail(code,r=b'',h=b'',t=b''):
 d={'schema':'tgw-nix-input-observation-bootstrap-failure/v1','request_sha256':'sha256:'+r.hex() if len(r)==32 else 'unknown'}
 d.update({'helper_sha256':'sha256:'+h.hex() if len(h)==32 else 'unknown','tool_manifest_sha256':'sha256:'+t.hex() if len(t)==32 else 'unknown'})
 d.update({'stage':'bootstrap','code':code,'outcome':'FAILED','cleanup':'removed','netns_inode':None})
 d['receipt_sha256']='sha256:'+hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 sys.stdout.write(json.dumps(d,sort_keys=True,separators=(',',':'))); raise SystemExit(1)
if len(raw)!=P.size: fail('TRUNCATED_PREFIX')
magic,version,flen,hlen,plen,rh,hh,th=P.unpack(raw)
if magic!=b'TGWNIXO1' or version!=1: fail('BAD_PREFIX',rh,hh,th)
if flen!=0 or hlen>1048576 or plen>65536 or min(hlen,plen)<1: fail('BAD_LENGTH',rh,hh,th)
helper=sys.stdin.buffer.read(hlen); payload=sys.stdin.buffer.read(plen)
if len(helper)!=hlen or len(payload)!=plen: fail('TRUNCATED_BODY',rh,hh,th)
if hashlib.sha256(helper).digest()!=hh or hashlib.sha256(payload).digest()!=rh: fail('HASH_MISMATCH',rh,hh,th)
try: q=json.loads(payload)
except Exception: fail('BAD_PAYLOAD_JSON',rh,hh,th)
if hashlib.sha256(json.dumps(q['tool_sha256'],sort_keys=True,separators=(',',':')).encode()).digest()!=th: fail('TOOL_HASH_MISMATCH',rh,hh,th)
rest=sys.stdin.buffer.read(); sys.stdin=io.TextIOWrapper(io.BytesIO(struct.pack('!Q',plen)+payload+rest))
g={'__name__':'__main__','_BOOTSTRAP_REQUEST_SHA256':'sha256:'+rh.hex()}
g.update({'_BOOTSTRAP_HELPER_SHA256':'sha256:'+hh.hex(),'_BOOTSTRAP_TOOL_MANIFEST_SHA256':'sha256:'+th.hex()})
exec(compile(helper,'<tgw-nix-input-observer>','exec'),g)"""


class NixInputObservationError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def helper_command(*, known_tool_sha256: dict[str, str]) -> list[str]:
    if set(known_tool_sha256) != set(TOOLS) or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in known_tool_sha256.values()):
        raise NixInputObservationError("exact observer tool identities are required")
    return [
        SUDO,
        "-n",
        "--",
        LAUNCHER,
    ]


def packet(helper_source: bytes, request: dict[str, Any], archive: Path) -> bytes:
    raw = _canonical(request)
    prefix = PREFIX.pack(MAGIC, 1, 0, len(helper_source), len(raw), hashlib.sha256(raw).digest(), hashlib.sha256(helper_source).digest(), hashlib.sha256(_canonical(request["tool_sha256"])).digest())
    return prefix + helper_source + raw + archive.read_bytes()


def validate_receipt(value: Any, *, request: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema",
        "request",
        "namespace",
        "process",
        "tools",
        "negative_probes_before",
        "negative_probes_after",
        "lock_nodes",
        "forced_inputs",
        "evaluated_drv",
        "observed_outputs",
        "cleanup",
        "nix_version",
        "receipt_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields or value["schema"] != SCHEMA or value["request"] != request:
        raise NixInputObservationError("observer receipt schema or request binding is invalid")
    namespace = value["namespace"]
    if (
        not isinstance(namespace, dict)
        or set(namespace) != {"start_inode", "end_inode", "loopback", "other_links", "routes", "link_json", "route_json", "link_json_sha256", "route_json_sha256", "held_for_entire_run"}
        or not isinstance(namespace["start_inode"], int)
        or namespace["start_inode"] != namespace["end_inode"]
        or namespace["held_for_entire_run"] is not True
        or namespace["loopback"] != "down"
        or namespace["other_links"] != []
        or namespace["routes"] != []
        or not isinstance(namespace["link_json"], list)
        or not isinstance(namespace["route_json"], list)
        or "sha256:" + hashlib.sha256(_canonical(namespace["link_json"])).hexdigest() != namespace["link_json_sha256"]
        or "sha256:" + hashlib.sha256(_canonical(namespace["route_json"])).hexdigest() != namespace["route_json_sha256"]
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", namespace["link_json_sha256"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", namespace["route_json_sha256"])
    ):
        raise NixInputObservationError("observer namespace evidence is invalid")
    probes = {"dns": "denied", "public_https": "denied", "private": "denied", "metadata": "denied"}
    if value["negative_probes_before"] != probes or value["negative_probes_after"] != probes:
        raise NixInputObservationError("observer negative probes are incomplete")
    process = value["process"]
    if (
        not isinstance(process, dict)
        or set(process) != {"pid", "starttime", "exe_sha256", "uid", "euid", "gid", "egid", "cgroup", "cap_eff", "cap_prm", "cap_inh", "cap_amb", "no_new_privs"}
        or not all(isinstance(process[key], int) and process[key] > 0 for key in ("pid", "starttime"))
        or process["exe_sha256"] != request["tool_sha256"]["python"]
        or process["uid"] != 1004
        or process["euid"] != 1004
        or process["gid"] != 1004
        or process["egid"] != 1004
        or any(process[key] != "0000000000000000" for key in ("cap_eff", "cap_prm", "cap_inh", "cap_amb"))
        or process["no_new_privs"] != 1
        or not isinstance(process["cgroup"], str)
        or not process["cgroup"].startswith("0::/")
    ):
        raise NixInputObservationError("observer process identity is invalid")
    if value["tools"] != request["tool_sha256"] or value["nix_version"] != "nix (Nix) 2.28.5" or value["lock_nodes"] != [{"node": NODE, "rev": REV, "nar_hash": LOCK_NAR}]:
        raise NixInputObservationError("observer tool or lock binding is invalid")
    inputs = value["forced_inputs"]
    if (
        not isinstance(inputs, list)
        or len(inputs) != 1
        or set(inputs[0]) != {"lock_node", "lock_rev", "lock_nar_hash", "path", "nar_sha256"}
        or inputs[0]["lock_node"] != NODE
        or inputs[0]["lock_rev"] != REV
        or inputs[0]["lock_nar_hash"] != LOCK_NAR
        or not STORE_PATH.fullmatch(inputs[0]["path"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", inputs[0]["nar_sha256"])
    ):
        raise NixInputObservationError("observer exact forced input is invalid")
    if not STORE_PATH.fullmatch(value["evaluated_drv"]):
        raise NixInputObservationError("observer derivation identity is invalid")
    additions = value["observed_outputs"]
    if (
        not isinstance(additions, list)
        or additions != sorted(additions, key=lambda item: (item.get("role", ""), item.get("path", "")))
        or len({item.get("path") for item in additions}) != len(additions)
        or len(additions) > 4
        or any(
            set(item) != {"role", "path", "nar_sha256"} or item["role"] != "derivation" or not STORE_PATH.fullmatch(item["path"]) or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["nar_sha256"])
            for item in additions
        )
        or not any(item["role"] == "derivation" and item["path"] == value["evaluated_drv"] for item in additions)
    ):
        raise NixInputObservationError("observer attributable store additions are invalid")
    if value["cleanup"] != "removed":
        raise NixInputObservationError("observer cleanup is not terminally verified")
    unsigned = dict(value)
    claimed = unsigned.pop("receipt_sha256", None)
    if claimed != "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest():
        raise NixInputObservationError("observer receipt self-hash mismatch")
    return dict(value)


def observe_archive(
    archive: Path,
    *,
    request: dict[str, Any],
    known_tool_sha256: dict[str, str],
    helper_source: bytes,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    """Invoke exactly one standalone helper; it owns extraction and every Nix step."""
    if (
        set(request) != {"source_archive_sha256", "observer_source_sha256", "source_commit", "source_tree", "flake_lock_sha256", "module_sha256", "launcher_descriptor_sha256", "sudo_rule_sha256"}
        or request.get("source_archive_sha256") != "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
        or request.get("observer_source_sha256") != "sha256:" + hashlib.sha256(helper_source).hexdigest()
        or not isinstance(request.get("source_commit"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", request["source_commit"])
        or not isinstance(request.get("source_tree"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", request["source_tree"])
        or not isinstance(request.get("flake_lock_sha256"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", request["flake_lock_sha256"])
        or not isinstance(request.get("module_sha256"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", request["module_sha256"])
        or not isinstance(request.get("launcher_descriptor_sha256"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", request["launcher_descriptor_sha256"])
        or not isinstance(request.get("sudo_rule_sha256"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", request["sudo_rule_sha256"])
        or set(known_tool_sha256) != set(TOOLS)
        or any(not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in known_tool_sha256.values())
    ):
        raise NixInputObservationError("observer immutable archive request is invalid")
    tool_fds = {}
    try:
        if run is subprocess.run:
            for name, path in TOOLS.items():
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                metadata = os.fstat(fd)
                if not os.path.isfile(f"/proc/self/fd/{fd}") or "sha256:" + hashlib.sha256(os.read(fd, metadata.st_size)).hexdigest() != known_tool_sha256[name]:
                    raise NixInputObservationError("held tool identity mismatch")
                os.lseek(fd, 0, os.SEEK_SET)
                tool_fds[name] = fd
            stable_paths = {name: f"/proc/{os.getpid()}/fd/{fd}" for name, fd in tool_fds.items()}
        else:
            stable_paths = dict(TOOLS)
        command = helper_command(known_tool_sha256=known_tool_sha256)
        command[0] = stable_paths["sudo"]
        bound_request = {**request, "tool_sha256": known_tool_sha256, "tool_paths": stable_paths}
        completed = run(command, input=packet(helper_source, bound_request, archive), capture_output=True, timeout=180, check=False, pass_fds=tuple(tool_fds.values()))
    finally:
        for fd in tool_fds.values():
            os.close(fd)
    if len(completed.stdout) > 2 * 1024 * 1024:
        raise NixInputObservationError("standalone zero-fetch observer output exceeded its bound")
    try:
        value = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NixInputObservationError("standalone observer returned malformed JSON") from exc
    if completed.returncode != 0:
        unsigned = dict(value) if isinstance(value, dict) else {}
        claimed = unsigned.pop("receipt_sha256", None)
        expected_request_hash = "sha256:" + hashlib.sha256(_canonical(bound_request)).hexdigest()
        expected_tool_hash = "sha256:" + hashlib.sha256(_canonical(known_tool_sha256)).hexdigest()
        common = (
            set(unsigned) == {"schema", "request_sha256", "helper_sha256", "tool_manifest_sha256", "stage", "code", "outcome", "cleanup", "netns_inode"}
            and unsigned.get("request_sha256") == expected_request_hash
            and unsigned.get("helper_sha256") == request["observer_source_sha256"]
            and unsigned.get("tool_manifest_sha256") == expected_tool_hash
            and claimed == "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
        )
        if unsigned.get("schema") == "tgw-nix-input-observation-bootstrap-failure/v1":
            valid = (
                common
                and unsigned.get("stage") == "bootstrap"
                and unsigned.get("code") in {"TRUNCATED_PREFIX", "BAD_PREFIX", "BAD_LENGTH", "TRUNCATED_BODY", "HASH_MISMATCH", "BAD_PAYLOAD_JSON", "TOOL_HASH_MISMATCH"}
                and unsigned.get("outcome") == "FAILED"
                and unsigned.get("cleanup") == "removed"
                and unsigned.get("netns_inode") is None
            )
        elif unsigned.get("schema") == "tgw-nix-input-observation-failure/v1":
            stages = {"request", "archive", "namespace", "nix", "cleanup"}
            codes = {"REQUEST_VALIDATION_FAILED", "NixInputObservationError", "JSONDecodeError", "TarError", "OSError", "UnicodeDecodeError"}
            cleanup = unsigned.get("cleanup")
            valid = (
                common
                and unsigned.get("stage") in stages
                and unsigned.get("code") in codes
                and cleanup in {"removed", "ambiguous"}
                and unsigned.get("outcome") == ("FAILED" if cleanup == "removed" else "AMBIGUOUS")
                and (unsigned.get("netns_inode") is None if unsigned.get("stage") in {"request", "archive"} else isinstance(unsigned.get("netns_inode"), int) and unsigned["netns_inode"] > 0)
            )
        else:
            valid = False
        if not valid:
            raise NixInputObservationError("standalone observer failure receipt is invalid")
        raise NixInputObservationError("standalone zero-fetch observer returned a validated failure")
    return validate_receipt(value, request=bound_request)


def _run(argv: list[str], cwd: Path) -> str:
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=120, check=False)
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        raise NixInputObservationError("fixed observer command failed")
    return result.stdout


def _strict_extract(archive: Path, target: Path, request: dict[str, Any]) -> Path:
    with tarfile.open(archive, "r:*") as source:
        members = source.getmembers()
        if not members or len(members) > 10_000 or source.pax_headers.get("comment") != request["source_commit"]:
            raise NixInputObservationError("archive identity is invalid")
        seen = set()
        total = 0
        for member in members:
            path = Path(member.name)
            normalized = path.as_posix().rstrip("/")
            if (
                path.is_absolute()
                or ".." in path.parts
                or ".git" in path.parts
                or not path.parts
                or path.parts[0] != "trader-grims-warehouse"
                or normalized in seen
                or not (member.isfile() or member.isdir())
            ):
                raise NixInputObservationError("archive member is unsafe")
            seen.add(normalized)
            total += member.size
        if total > 64 * 1024 * 1024:
            raise NixInputObservationError("archive is oversized")
        source.extractall(target, filter="data")
    tree = target / "trader-grims-warehouse"
    git = [GIT, "-c", "core.hooksPath=/dev/null", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.required=false"]
    _run(git + ["init", "-q"], tree)
    _run(git + ["add", "-f", "-A"], tree)
    if _run(git + ["write-tree"], tree).strip() != request["source_tree"]:
        raise NixInputObservationError("archive tree is invalid")
    for name, field in (("flake.lock", "flake_lock_sha256"), ("nix/review-egress.nix", "module_sha256")):
        if "sha256:" + hashlib.sha256((tree / name).read_bytes()).hexdigest() != request[field]:
            raise NixInputObservationError("archive bound file digest mismatch")
    return tree


def _probe_denied(host: str, port: int) -> str:
    try:
        socket.create_connection((host, port), timeout=0.2).close()
    except OSError:
        return "denied"
    raise NixInputObservationError("network negative probe unexpectedly connected")


def _all_probes() -> dict[str, str]:
    return {
        "dns": _probe_denied("1.1.1.1", 53),
        "public_https": _probe_denied("1.1.1.1", 443),
        "private": _probe_denied("10.0.0.1", 443),
        "metadata": _probe_denied("169.254.169.254", 80),
    }


def _valid_store_path(path: str, cwd: Path) -> bool:
    result = subprocess.run([NIX_STORE, "--check-validity", path], cwd=cwd, capture_output=True, timeout=30, check=False)
    return result.returncode == 0


def standalone_main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="tgw-nix-observe-"))
    request: dict[str, Any] = {}
    stage = "request"
    cleanup_result = "unknown"
    try:

        def enter(value: str) -> None:
            nonlocal stage
            stage = value
            hook = globals().get("_OBSERVER_STAGE_HOOK")
            if hook is not None:
                hook(value)

        netns_start = os.stat("/proc/self/ns/net").st_ino
        raw_size = struct.unpack("!Q", sys.stdin.buffer.read(8))[0]
        if not 1 <= raw_size <= 64 * 1024:
            raise NixInputObservationError("bootstrap payload size is invalid")
        raw = sys.stdin.buffer.read(raw_size)
        request = json.loads(raw)
        bootstrap_request_hash = globals().get("_BOOTSTRAP_REQUEST_SHA256", "unknown")
        bootstrap_helper_hash = globals().get("_BOOTSTRAP_HELPER_SHA256", "unknown")
        bootstrap_tool_hash = globals().get("_BOOTSTRAP_TOOL_MANIFEST_SHA256", "unknown")
        if (
            bootstrap_request_hash != "sha256:" + hashlib.sha256(_canonical(request)).hexdigest()
            or bootstrap_helper_hash != request.get("observer_source_sha256")
            or bootstrap_tool_hash != "sha256:" + hashlib.sha256(_canonical(request.get("tool_sha256"))).hexdigest()
            or os.environ.get("TGW_OBSERVER_DESCRIPTOR_SHA256") != request.get("launcher_descriptor_sha256")
            or os.environ.get("TGW_OBSERVER_SUDO_RULE_SHA256") != request.get("sudo_rule_sha256")
        ):
            raise NixInputObservationError("bootstrap/helper identity cross-check failed")
        expected = {
            "source_archive_sha256",
            "observer_source_sha256",
            "source_commit",
            "source_tree",
            "flake_lock_sha256",
            "module_sha256",
            "launcher_descriptor_sha256",
            "sudo_rule_sha256",
            "tool_sha256",
            "tool_paths",
        }
        if (
            set(request) != expected
            or not all(isinstance(request[key], str) for key in expected - {"tool_sha256", "tool_paths"})
            or not isinstance(request["tool_sha256"], dict)
            or not isinstance(request["tool_paths"], dict)
            or set(request["tool_sha256"]) != set(TOOLS)
            or set(request["tool_paths"]) != set(TOOLS)
            or any(not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in request["tool_sha256"].values())
            or any(not isinstance(value, str) or not value.startswith("/") for value in request["tool_paths"].values())
            or not re.fullmatch(r"[0-9a-f]{40}", request["source_commit"])
            or not re.fullmatch(r"[0-9a-f]{40}", request["source_tree"])
            or any(
                not re.fullmatch(r"sha256:[0-9a-f]{64}", request[key])
                for key in ("source_archive_sha256", "observer_source_sha256", "flake_lock_sha256", "module_sha256", "launcher_descriptor_sha256", "sudo_rule_sha256")
            )
        ):
            raise NixInputObservationError("REQUEST_VALIDATION_FAILED")
        enter("archive")
        archive = scratch / "source.tar"
        archive.write_bytes(sys.stdin.buffer.read())
        if "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest() != request["source_archive_sha256"]:
            raise NixInputObservationError("archive digest mismatch")
        if set(request["tool_paths"]) != set(TOOLS):
            raise NixInputObservationError("tool path schema mismatch")
        globals().update({name.upper(): path for name, path in request["tool_paths"].items()})
        globals()["TOOLS"] = dict(request["tool_paths"])
        tools = {name: "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest() for name, path in TOOLS.items()}
        if tools != request["tool_sha256"]:
            raise NixInputObservationError("tool identity mismatch")
        tree = _strict_extract(archive, scratch / "extract", request)
        enter("namespace")
        links = json.loads(_run([IP, "-json", "link", "show"], tree))
        routes = json.loads(_run([IP, "-json", "route", "show"], tree))
        if routes or len(links) != 1 or links[0].get("ifname") != "lo" or links[0].get("operstate") not in {"DOWN", "UNKNOWN"} or "UP" in links[0].get("flags", []):
            raise NixInputObservationError("namespace network state is not isolated")
        link_hash = "sha256:" + hashlib.sha256(_canonical(links)).hexdigest()
        route_hash = "sha256:" + hashlib.sha256(_canonical(routes)).hexdigest()
        probes_before = _all_probes()
        enter("nix")
        base = [NIX, "--offline", "--option", "substituters", "", "--option", "allow-import-from-derivation", "false", "--option", "pure-eval", "true", "--no-write-lock-file"]
        metadata = json.loads(_run(base + ["flake", "metadata", "--json", "path:."], tree))
        locked = metadata["locks"]["nodes"]
        if set(locked) != {"root", NODE} or locked[NODE]["locked"]["rev"] != REV or locked[NODE]["locked"]["narHash"] != LOCK_NAR:
            raise NixInputObservationError("lock graph mismatch")
        input_path = _run(base + ["eval", "--raw", ".#inputIdentities.nixpkgs.outPath"], tree).strip()
        input_nar = _run(base + ["hash", "path", "--type", "sha256", "--base16", input_path], tree).strip()
        drv_target = ".#packages.x86_64-linux.review-egress-systemd-units.drvPath"
        drv = _run(base + ["eval", "--raw", drv_target], tree).strip()
        observed_outputs = []
        for role, path in (("derivation", drv),):
            if not _valid_store_path(path, tree):
                raise NixInputObservationError("evaluated derivation is not a valid observed output")
            observed_outputs.append({"role": role, "path": path, "nar_sha256": "sha256:" + _run(base + ["hash", "path", "--type", "sha256", "--base16", path], tree).strip()})
        probes_after = _all_probes()
        netns_end = os.stat("/proc/self/ns/net").st_ino
        stat_fields = Path("/proc/self/stat").read_text().split()
        status = {key.rstrip(":"): value.strip() for key, value in (line.split(maxsplit=1) for line in Path("/proc/self/status").read_text().splitlines() if "\t" in line)}
        value = {
            "schema": SCHEMA,
            "request": request,
            "namespace": {
                "start_inode": netns_start,
                "end_inode": netns_end,
                "loopback": "down",
                "other_links": [],
                "routes": [],
                "link_json": links,
                "route_json": routes,
                "link_json_sha256": link_hash,
                "route_json_sha256": route_hash,
                "held_for_entire_run": True,
            },
            "process": {
                "pid": os.getpid(),
                "starttime": int(stat_fields[21]),
                "exe_sha256": "sha256:" + hashlib.sha256(Path("/proc/self/exe").read_bytes()).hexdigest(),
                "uid": os.getuid(),
                "euid": os.geteuid(),
                "gid": os.getgid(),
                "egid": os.getegid(),
                "cgroup": Path("/proc/self/cgroup").read_text().strip(),
                "cap_eff": status["CapEff"],
                "cap_prm": status["CapPrm"],
                "cap_inh": status["CapInh"],
                "cap_amb": status["CapAmb"],
                "no_new_privs": int(status["NoNewPrivs"]),
            },
            "tools": tools,
            "negative_probes_before": probes_before,
            "negative_probes_after": probes_after,
            "lock_nodes": [{"node": NODE, "rev": REV, "nar_hash": LOCK_NAR}],
            "forced_inputs": [{"lock_node": NODE, "lock_rev": REV, "lock_nar_hash": LOCK_NAR, "path": input_path, "nar_sha256": "sha256:" + input_nar}],
            "evaluated_drv": drv,
            "observed_outputs": observed_outputs,
            "nix_version": _run([NIX, "--version"], tree).strip(),
        }
        success_value = value
    except Exception as exc:
        failure_exc = exc
    finally:
        try:
            globals().get("_OBSERVER_CLEANUP", shutil.rmtree)(scratch)
            cleanup_result = "removed" if not scratch.exists() else "ambiguous"
        except Exception:
            cleanup_result = "ambiguous"
    if "failure_exc" not in locals() and cleanup_result == "removed":
        success_value["cleanup"] = "removed"
        success_value["receipt_sha256"] = "sha256:" + hashlib.sha256(_canonical(success_value)).hexdigest()
        sys.stdout.write(json.dumps(success_value, sort_keys=True, separators=(",", ":")))
        return 0
    if "failure_exc" not in locals():
        failure_exc = NixInputObservationError("cleanup outcome ambiguous")
        stage = "cleanup"
    if "failure_exc" in locals():
        failure = {
            "schema": "tgw-nix-input-observation-failure/v1",
            "request_sha256": globals().get("_BOOTSTRAP_REQUEST_SHA256", "unknown"),
            "helper_sha256": globals().get("_BOOTSTRAP_HELPER_SHA256", "unknown"),
            "tool_manifest_sha256": globals().get("_BOOTSTRAP_TOOL_MANIFEST_SHA256", "unknown"),
            "stage": stage,
            "code": "REQUEST_VALIDATION_FAILED" if stage == "request" else type(failure_exc).__name__,
            "outcome": "AMBIGUOUS" if cleanup_result == "ambiguous" else "FAILED",
            "cleanup": cleanup_result,
            "netns_inode": None if stage in {"request", "archive"} else os.stat("/proc/self/ns/net").st_ino,
        }
        failure["receipt_sha256"] = "sha256:" + hashlib.sha256(_canonical(failure)).hexdigest()
        sys.stdout.write(json.dumps(failure, sort_keys=True, separators=(",", ":")))
        return 2 if cleanup_result == "ambiguous" else 1


if __name__ == "__main__":
    raise SystemExit(standalone_main())
