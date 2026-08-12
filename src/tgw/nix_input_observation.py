"""Bounded, zero-network observation of the reviewed-evaluation Nix input."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

SCHEMA = "tgw-nix-input-observation/v1"
NIX = "/run/current-system/sw/bin/nix"
UNSHARE = "/run/current-system/sw/bin/unshare"
NODE = "nixpkgs"
REV = "ac62194c3917d5f474c1a844b6fd6da2db95077d"
LOCK_NAR = "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w="
STORE_PATH = re.compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+")


class NixInputObservationError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _command(*tail: str) -> list[str]:
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
        NIX,
        "--offline",
        "--option",
        "substituters",
        "",
        "--option",
        "allow-import-from-derivation",
        "false",
        "--option",
        "pure-eval",
        "true",
        "--no-write-lock-file",
        *tail,
    ]


def validate_receipt(value: Any, *, source_commit: str, source_tree: str, flake_lock_sha256: str) -> dict[str, Any]:
    fields = {
        "schema",
        "source_commit",
        "source_tree",
        "flake_lock_sha256",
        "network_namespace",
        "direct_egress",
        "nix_remote",
        "lock_nodes",
        "forced_inputs",
        "evaluated_drv",
        "store_additions",
        "commands",
        "receipt_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise NixInputObservationError("observation receipt schema is invalid")
    if value["schema"] != SCHEMA or (value["source_commit"], value["source_tree"], value["flake_lock_sha256"]) != (source_commit, source_tree, flake_lock_sha256):
        raise NixInputObservationError("observation source binding mismatch")
    if value["network_namespace"] != "fresh-unshare-net" or value["direct_egress"] is not False or value["nix_remote"] != "local":
        raise NixInputObservationError("observation isolation claim is invalid")
    expected_lock = [{"node": NODE, "rev": REV, "nar_hash": LOCK_NAR}]
    if value["lock_nodes"] != expected_lock or not isinstance(value["forced_inputs"], list) or len(value["forced_inputs"]) != 1:
        raise NixInputObservationError("observation exact input set is invalid")
    item = value["forced_inputs"][0]
    if (
        not isinstance(item, dict)
        or set(item) != {"lock_node", "lock_rev", "lock_nar_hash", "path", "nar_sha256"}
        or item["lock_node"] != NODE
        or item["lock_rev"] != REV
        or item["lock_nar_hash"] != LOCK_NAR
        or not STORE_PATH.fullmatch(item["path"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["nar_sha256"])
    ):
        raise NixInputObservationError("observation input identity is invalid")
    if value["store_additions"] != [] or not STORE_PATH.fullmatch(value["evaluated_drv"]):
        raise NixInputObservationError("observation wrote to the store or omitted drv identity")
    unsigned = dict(value)
    claimed = unsigned.pop("receipt_sha256", None)
    if claimed != "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest():
        raise NixInputObservationError("observation receipt self-hash mismatch")
    return dict(value)


def observe(
    source: Path,
    *,
    source_commit: str,
    source_tree: str,
    flake_lock_sha256: str,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Observe only already-present inputs; any store addition or failed command refuses."""
    commands = {
        "store_before": _command("path-info", "--all", "--json"),
        "metadata": _command("flake", "metadata", "--json", "path:."),
        "input_path": _command("eval", "--raw", ".#inputIdentities.nixpkgs.outPath"),
        "input_nar": None,
        "drv": _command("eval", "--raw", ".#packages.x86_64-linux.review-egress-systemd-units.drvPath"),
        "store_after": _command("path-info", "--all", "--json"),
    }

    def invoke(command: list[str]) -> str:
        completed = run(command, cwd=source, text=True, capture_output=True, timeout=120, check=False)
        if completed.returncode != 0 or len(completed.stdout) > 8 * 1024 * 1024:
            raise NixInputObservationError("zero-fetch observation command failed")
        return completed.stdout

    before = set(json.loads(invoke(commands["store_before"])))
    metadata = json.loads(invoke(commands["metadata"]))
    locked = metadata.get("locks", {}).get("nodes", {}).get(NODE, {}).get("locked", {})
    if locked.get("rev") != REV or locked.get("narHash") != LOCK_NAR or set(metadata.get("locks", {}).get("nodes", {})) != {"root", NODE}:
        raise NixInputObservationError("offline metadata lock graph mismatch")
    input_path = invoke(commands["input_path"]).strip()
    if not STORE_PATH.fullmatch(input_path):
        raise NixInputObservationError("offline input path is invalid")
    commands["input_nar"] = _command("hash", "path", "--type", "sha256", "--base16", input_path)
    input_nar = invoke(commands["input_nar"]).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", input_nar):
        raise NixInputObservationError("offline input NAR hash is invalid")
    drv = invoke(commands["drv"]).strip()
    after = set(json.loads(invoke(commands["store_after"])))
    receipt = {
        "schema": SCHEMA,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "flake_lock_sha256": flake_lock_sha256,
        "network_namespace": "fresh-unshare-net",
        "direct_egress": False,
        "nix_remote": "local",
        "lock_nodes": [{"node": NODE, "rev": REV, "nar_hash": LOCK_NAR}],
        "forced_inputs": [{"lock_node": NODE, "lock_rev": REV, "lock_nar_hash": LOCK_NAR, "path": input_path, "nar_sha256": "sha256:" + input_nar}],
        "evaluated_drv": drv,
        "store_additions": sorted(after - before),
        "commands": commands,
    }
    receipt["receipt_sha256"] = "sha256:" + hashlib.sha256(_canonical(receipt)).hexdigest()
    return validate_receipt(receipt, source_commit=source_commit, source_tree=source_tree, flake_lock_sha256=flake_lock_sha256)
