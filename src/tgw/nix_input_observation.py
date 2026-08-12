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
PYTHON = "/run/current-system/sw/bin/python3"
UNSHARE = "/run/current-system/sw/bin/unshare"
IP = "/run/current-system/sw/bin/ip"
NIX = "/run/current-system/sw/bin/nix"
NIX_STORE = "/run/current-system/sw/bin/nix-store"
GIT = "/run/current-system/sw/bin/git"
TOOLS = {"unshare": UNSHARE, "ip": IP, "python": PYTHON, "nix": NIX, "nix_store": NIX_STORE, "git": GIT}
NODE = "nixpkgs"
REV = "ac62194c3917d5f474c1a844b6fd6da2db95077d"
LOCK_NAR = "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w="
STORE_PATH = re.compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+(?:\.drv)?")
BOOTSTRAP = (
    "import hashlib,struct,sys; n=struct.unpack('!Q',sys.stdin.buffer.read(8))[0]; "
    "s=sys.stdin.buffer.read(n); h=sys.stdin.buffer.read(64).decode(); "
    "assert hashlib.sha256(s).hexdigest()==h; "
    "exec(compile(s,'<tgw-nix-input-observer>','exec'),{'__name__':'__main__'})"
)


class NixInputObservationError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def helper_command(*, known_tool_sha256: dict[str, str]) -> list[str]:
    if set(known_tool_sha256) != set(TOOLS) or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in known_tool_sha256.values()):
        raise NixInputObservationError("exact observer tool identities are required")
    return [
        UNSHARE,
        "--net",
        "--map-root-user",
        "--",
        "/usr/bin/env",
        "-i",
        "HOME=/var/empty",
        "NIX_REMOTE=local",
        "PATH=/run/current-system/sw/bin",
        PYTHON,
        "-I",
        "-c",
        BOOTSTRAP,
    ]


def packet(helper_source: bytes, request: dict[str, Any], archive: Path) -> bytes:
    raw = _canonical(request)
    return struct.pack("!Q", len(helper_source)) + helper_source + hashlib.sha256(helper_source).hexdigest().encode() + struct.pack("!Q", len(raw)) + raw + archive.read_bytes()


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
        "store_additions",
        "nix_version",
        "receipt_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields or value["schema"] != SCHEMA or value["request"] != request:
        raise NixInputObservationError("observer receipt schema or request binding is invalid")
    namespace = value["namespace"]
    if (
        not isinstance(namespace, dict)
        or set(namespace) != {"start_inode", "end_inode", "loopback", "other_links", "routes", "link_json_sha256", "route_json_sha256", "held_for_entire_run"}
        or not isinstance(namespace["start_inode"], int)
        or namespace["start_inode"] != namespace["end_inode"]
        or namespace["held_for_entire_run"] is not True
        or namespace["loopback"] != "down"
        or namespace["other_links"] != []
        or namespace["routes"] != []
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
        or set(process) != {"pid", "starttime", "exe_sha256"}
        or not all(isinstance(process[key], int) and process[key] > 0 for key in ("pid", "starttime"))
        or process["exe_sha256"] != request["tool_sha256"]["python"]
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
    additions = value["store_additions"]
    if (
        not isinstance(additions, list)
        or additions != sorted(additions, key=lambda item: (item.get("role", ""), item.get("path", "")))
        or len({item.get("path") for item in additions}) != len(additions)
        or len(additions) > 4
        or any(
            set(item) != {"role", "path", "nar_sha256", "preexisting"}
            or item["role"] not in {"source", "evaluation", "derivation"}
            or not STORE_PATH.fullmatch(item["path"])
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["nar_sha256"])
            or not isinstance(item["preexisting"], bool)
            for item in additions
        )
        or not any(item["role"] == "derivation" and item["path"] == value["evaluated_drv"] for item in additions)
    ):
        raise NixInputObservationError("observer attributable store additions are invalid")
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
        set(request) != {"source_archive_sha256", "observer_source_sha256", "source_commit", "source_tree", "flake_lock_sha256", "module_sha256"}
        or request.get("source_archive_sha256") != "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
        or request.get("observer_source_sha256") != "sha256:" + hashlib.sha256(helper_source).hexdigest()
        or request.get("source_commit") is None
        or request.get("source_tree") is None
        or request.get("flake_lock_sha256") is None
        or request.get("module_sha256") is None
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
        command[0] = stable_paths["unshare"]
        command[-4] = stable_paths["python"]
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
        if (
            set(unsigned) != {"schema", "request_sha256", "helper_sha256", "tools", "stage", "code", "cleanup", "netns_inode"}
            or unsigned.get("schema") != "tgw-nix-input-observation-failure/v1"
            or unsigned.get("cleanup") not in {"removed", "ambiguous"}
            or unsigned.get("request_sha256") != "sha256:" + hashlib.sha256(_canonical(bound_request)).hexdigest()
            or unsigned.get("helper_sha256") != request["observer_source_sha256"]
            or unsigned.get("tools") != known_tool_sha256
            or claimed != "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
        ):
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
        netns_start = os.stat("/proc/self/ns/net").st_ino
        raw_size = struct.unpack("!Q", sys.stdin.buffer.read(8))[0]
        request = json.loads(sys.stdin.buffer.read(raw_size))
        expected = {"source_archive_sha256", "observer_source_sha256", "source_commit", "source_tree", "flake_lock_sha256", "module_sha256", "tool_sha256", "tool_paths"}
        if set(request) != expected or not all(isinstance(request[key], (str, dict)) for key in expected):
            raise NixInputObservationError("request schema mismatch")
        stage = "archive"
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
        stage = "namespace"
        links = json.loads(_run([IP, "-json", "link", "show"], tree))
        routes = json.loads(_run([IP, "-json", "route", "show"], tree))
        if routes or len(links) != 1 or links[0].get("ifname") != "lo" or links[0].get("operstate") not in {"DOWN", "UNKNOWN"} or "UP" in links[0].get("flags", []):
            raise NixInputObservationError("namespace network state is not isolated")
        link_hash = "sha256:" + hashlib.sha256(_canonical(links)).hexdigest()
        route_hash = "sha256:" + hashlib.sha256(_canonical(routes)).hexdigest()
        probes_before = _all_probes()
        stage = "nix"
        base = [NIX, "--offline", "--option", "substituters", "", "--option", "allow-import-from-derivation", "false", "--option", "pure-eval", "true", "--no-write-lock-file"]
        metadata = json.loads(_run(base + ["flake", "metadata", "--json", "path:."], tree))
        locked = metadata["locks"]["nodes"]
        if set(locked) != {"root", NODE} or locked[NODE]["locked"]["rev"] != REV or locked[NODE]["locked"]["narHash"] != LOCK_NAR:
            raise NixInputObservationError("lock graph mismatch")
        input_path = _run(base + ["eval", "--raw", ".#inputIdentities.nixpkgs.outPath"], tree).strip()
        input_nar = _run(base + ["hash", "path", "--type", "sha256", "--base16", input_path], tree).strip()
        drv_target = ".#packages.x86_64-linux.review-egress-systemd-units.drvPath"
        drv = _run(base + ["eval", "--raw", drv_target], tree).strip()
        additions = []
        for role, path in (("derivation", drv),):
            additions.append(
                {"role": role, "path": path, "nar_sha256": "sha256:" + _run(base + ["hash", "path", "--type", "sha256", "--base16", path], tree).strip(), "preexisting": _valid_store_path(path, tree)}
            )
        probes_after = _all_probes()
        netns_end = os.stat("/proc/self/ns/net").st_ino
        stat_fields = Path("/proc/self/stat").read_text().split()
        value = {
            "schema": SCHEMA,
            "request": request,
            "namespace": {
                "start_inode": netns_start,
                "end_inode": netns_end,
                "loopback": "down",
                "other_links": [],
                "routes": [],
                "link_json_sha256": link_hash,
                "route_json_sha256": route_hash,
                "held_for_entire_run": True,
            },
            "process": {"pid": os.getpid(), "starttime": int(stat_fields[21]), "exe_sha256": tools["python"]},
            "tools": tools,
            "negative_probes_before": probes_before,
            "negative_probes_after": probes_after,
            "lock_nodes": [{"node": NODE, "rev": REV, "nar_hash": LOCK_NAR}],
            "forced_inputs": [{"lock_node": NODE, "lock_rev": REV, "lock_nar_hash": LOCK_NAR, "path": input_path, "nar_sha256": "sha256:" + input_nar}],
            "evaluated_drv": drv,
            "store_additions": additions,
            "nix_version": _run([NIX, "--version"], tree).strip(),
        }
        value["receipt_sha256"] = "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()
        sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        failure_exc = exc
    finally:
        try:
            shutil.rmtree(scratch)
            cleanup_result = "removed" if not scratch.exists() else "ambiguous"
        except Exception:
            cleanup_result = "ambiguous"
    if "failure_exc" in locals():
        failure = {
            "schema": "tgw-nix-input-observation-failure/v1",
            "request_sha256": "sha256:" + hashlib.sha256(_canonical(request)).hexdigest(),
            "helper_sha256": request.get("observer_source_sha256", "unknown"),
            "tools": request.get("tool_sha256", {}),
            "stage": stage,
            "code": type(failure_exc).__name__,
            "cleanup": cleanup_result,
            "netns_inode": os.stat("/proc/self/ns/net").st_ino,
        }
        failure["receipt_sha256"] = "sha256:" + hashlib.sha256(_canonical(failure)).hexdigest()
        sys.stdout.write(json.dumps(failure, sort_keys=True, separators=(",", ":")))
        return 2 if cleanup_result == "ambiguous" else 1


if __name__ == "__main__":
    raise SystemExit(standalone_main())
