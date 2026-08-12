import hashlib
import json
import subprocess

import pytest

from tgw.nix_input_observation import LOCK_NAR, NIX, REV, UNSHARE, NixInputObservationError, observe_archive

DIGEST = "a" * 64
TOOLS = {name: "sha256:" + DIGEST for name in ("unshare", "ip", "python", "nix", "nix_store")}


def request(archive, helper=b"# fixed standalone helper"):
    return {
        "source_archive_sha256": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
        "source_commit": "b" * 40,
        "source_tree": "c" * 40,
        "flake_lock_sha256": "sha256:" + "d" * 64,
        "module_sha256": "sha256:" + "e" * 64,
        "observer_source_sha256": "sha256:" + hashlib.sha256(helper).hexdigest(),
    }


def receipt(req):
    bound = {**req, "tool_sha256": TOOLS}
    value = {
        "schema": "tgw-nix-input-observation/v2",
        "request": bound,
        "namespace": {"inode": 42, "links": [], "routes": [], "held_for_entire_run": True},
        "process": {"pid": 123, "starttime": 456, "exe_sha256": TOOLS["python"]},
        "tools": TOOLS,
        "negative_probes": {"dns": "denied", "public_https": "denied", "private": "denied", "metadata": "denied"},
        "lock_nodes": [{"node": "nixpkgs", "rev": REV, "nar_hash": LOCK_NAR}],
        "forced_inputs": [{"lock_node": "nixpkgs", "lock_rev": REV, "lock_nar_hash": LOCK_NAR, "path": "/nix/store/11111111111111111111111111111111-source", "nar_sha256": "sha256:" + DIGEST}],
        "evaluated_drv": "/nix/store/22222222222222222222222222222222-review.drv",
        "store_additions": [{"role": "source", "path": "/nix/store/33333333333333333333333333333333-source", "nar_sha256": "sha256:" + DIGEST}],
        "nix_version": "2.28.5",
    }
    value["receipt_sha256"] = "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def test_one_helper_owns_archive_namespace_and_all_nix_steps(tmp_path):
    archive = tmp_path / "source.tar"
    archive.write_bytes(b"exact archive")
    req = request(archive)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(receipt(req)).encode(), b"")

    result = observe_archive(archive, request=req, known_tool_sha256=TOOLS, helper_source=b"# fixed standalone helper", run=run)
    assert result["namespace"]["held_for_entire_run"] is True and len(calls) == 1
    command, kwargs = calls[0]
    assert command[:4] == [UNSHARE, "--net", "--map-root-user", "--"]
    assert "NIX_REMOTE=local" in command and NIX not in command
    assert kwargs["timeout"] == 180 and kwargs["capture_output"] is True
    assert b"exact archive" in kwargs["input"]


def test_archive_and_receipt_tampering_fail_closed(tmp_path):
    archive = tmp_path / "source.tar"
    archive.write_bytes(b"archive")
    req = request(archive, b"helper")
    req["source_archive_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(NixInputObservationError, match="archive request"):
        observe_archive(archive, request=req, known_tool_sha256=TOOLS, helper_source=b"helper", run=lambda *a, **k: None)

    req = request(archive, b"helper")
    value = receipt(req)
    value["namespace"]["routes"] = ["default"]

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(value).encode(), b"")

    with pytest.raises(NixInputObservationError, match="namespace"):
        observe_archive(archive, request=req, known_tool_sha256=TOOLS, helper_source=b"helper", run=run)


def test_flake_native_input_resolution_has_no_unlocked_getflake():
    source = open("src/tgw/nixos_reviewed_evaluation.py").read()
    assert ".#inputIdentities.nixpkgs.outPath" in source and "builtins.getFlake" not in source
