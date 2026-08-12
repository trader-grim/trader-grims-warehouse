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
        "negative_probes",
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
        or set(namespace) != {"start_inode", "end_inode", "loopback", "other_links", "routes", "held_for_entire_run"}
        or not isinstance(namespace["start_inode"], int)
        or namespace["start_inode"] != namespace["end_inode"]
        or namespace["held_for_entire_run"] is not True
        or namespace["loopback"] != "down"
        or namespace["other_links"] != []
        or namespace["routes"] != []
    ):
        raise NixInputObservationError("observer namespace evidence is invalid")
    if value["negative_probes"] != {"dns": "denied", "public_https": "denied", "private": "denied", "metadata": "denied"}:
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
            set(item) != {"role", "path", "nar_sha256"}
            or item["role"] not in {"source", "evaluation", "derivation"}
            or not STORE_PATH.fullmatch(item["path"])
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["nar_sha256"])
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
    command = helper_command(known_tool_sha256=known_tool_sha256)
    bound_request = {**request, "tool_sha256": known_tool_sha256}
    completed = run(command, input=packet(helper_source, bound_request, archive), capture_output=True, timeout=180, check=False)
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
            set(unsigned) != {"schema", "code", "cleanup"}
            or unsigned.get("schema") != "tgw-nix-input-observation-failure/v1"
            or unsigned.get("cleanup") != "attempted"
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


def standalone_main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="tgw-nix-observe-"))
    try:
        raw_size = struct.unpack("!Q", sys.stdin.buffer.read(8))[0]
        request = json.loads(sys.stdin.buffer.read(raw_size))
        archive = scratch / "source.tar"
        archive.write_bytes(sys.stdin.buffer.read())
        if "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest() != request["source_archive_sha256"]:
            raise NixInputObservationError("archive digest mismatch")
        tools = {name: "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest() for name, path in TOOLS.items()}
        if tools != request["tool_sha256"]:
            raise NixInputObservationError("tool identity mismatch")
        tree = _strict_extract(archive, scratch / "extract", request)
        links = json.loads(_run([IP, "-json", "link", "show"], tree))
        routes = json.loads(_run([IP, "-json", "route", "show"], tree))
        if routes or {item.get("ifname") for item in links} != {"lo"}:
            raise NixInputObservationError("namespace network state is not isolated")
        base = [NIX, "--offline", "--option", "substituters", "", "--option", "allow-import-from-derivation", "false", "--option", "pure-eval", "true", "--no-write-lock-file"]
        metadata = json.loads(_run(base + ["flake", "metadata", "--json", "path:."], tree))
        locked = metadata["locks"]["nodes"]
        if set(locked) != {"root", NODE} or locked[NODE]["locked"]["rev"] != REV or locked[NODE]["locked"]["narHash"] != LOCK_NAR:
            raise NixInputObservationError("lock graph mismatch")
        input_path = _run(base + ["eval", "--raw", ".#inputIdentities.nixpkgs.outPath"], tree).strip()
        input_nar = _run(base + ["hash", "path", "--type", "sha256", "--base16", input_path], tree).strip()
        drv = _run(base + ["eval", "--raw", ".#packages.x86_64-linux.review-egress-systemd-units.drvPath"], tree).strip()
        additions = []
        for role, path in (("derivation", drv),):
            additions.append({"role": role, "path": path, "nar_sha256": "sha256:" + _run(base + ["hash", "path", "--type", "sha256", "--base16", path], tree).strip()})
        stat_fields = Path("/proc/self/stat").read_text().split()
        value = {
            "schema": SCHEMA,
            "request": request,
            "namespace": {
                "start_inode": os.stat("/proc/self/ns/net").st_ino,
                "end_inode": os.stat("/proc/self/ns/net").st_ino,
                "loopback": "down",
                "other_links": [],
                "routes": [],
                "held_for_entire_run": True,
            },
            "process": {"pid": os.getpid(), "starttime": int(stat_fields[21]), "exe_sha256": tools["python"]},
            "tools": tools,
            "negative_probes": {
                "dns": _probe_denied("1.1.1.1", 53),
                "public_https": _probe_denied("1.1.1.1", 443),
                "private": _probe_denied("10.0.0.1", 443),
                "metadata": _probe_denied("169.254.169.254", 80),
            },
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
        failure = {"schema": "tgw-nix-input-observation-failure/v1", "code": type(exc).__name__, "cleanup": "attempted"}
        failure["receipt_sha256"] = "sha256:" + hashlib.sha256(_canonical(failure)).hexdigest()
        sys.stdout.write(json.dumps(failure, sort_keys=True, separators=(",", ":")))
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(standalone_main())
