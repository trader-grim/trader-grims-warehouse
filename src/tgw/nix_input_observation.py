"""One-process, zero-egress observation of exact reviewed-evaluation inputs."""

from __future__ import annotations

import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path
from typing import Any, Callable

SCHEMA = "tgw-nix-input-observation/v2"
PYTHON = "/run/current-system/sw/bin/python3"
UNSHARE = "/run/current-system/sw/bin/unshare"
IP = "/run/current-system/sw/bin/ip"
NIX = "/run/current-system/sw/bin/nix"
NIX_STORE = "/run/current-system/sw/bin/nix-store"
TOOLS = {"unshare": UNSHARE, "ip": IP, "python": PYTHON, "nix": NIX, "nix_store": NIX_STORE}
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
        or set(namespace) != {"inode", "links", "routes", "held_for_entire_run"}
        or not isinstance(namespace["inode"], int)
        or namespace["held_for_entire_run"] is not True
        or namespace["links"] != []
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
    if value["tools"] != request["tool_sha256"] or value["lock_nodes"] != [{"node": NODE, "rev": REV, "nar_hash": LOCK_NAR}]:
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
        or len(additions) > 4
        or any(
            set(item) != {"role", "path", "nar_sha256"}
            or item["role"] not in {"source", "evaluation", "derivation"}
            or not STORE_PATH.fullmatch(item["path"])
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["nar_sha256"])
            for item in additions
        )
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
        request.get("source_archive_sha256") != "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
        or request.get("observer_source_sha256") != "sha256:" + hashlib.sha256(helper_source).hexdigest()
        or request.get("source_commit") is None
        or request.get("source_tree") is None
        or request.get("flake_lock_sha256") is None
        or request.get("module_sha256") is None
    ):
        raise NixInputObservationError("observer immutable archive request is invalid")
    command = helper_command(known_tool_sha256=known_tool_sha256)
    completed = run(command, input=packet(helper_source, request, archive), capture_output=True, timeout=180, check=False)
    if completed.returncode != 0 or len(completed.stdout) > 2 * 1024 * 1024:
        raise NixInputObservationError("standalone zero-fetch observer failed")
    try:
        value = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NixInputObservationError("standalone observer returned malformed JSON") from exc
    return validate_receipt(value, request={**request, "tool_sha256": known_tool_sha256})
