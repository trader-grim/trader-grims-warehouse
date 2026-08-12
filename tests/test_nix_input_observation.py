import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tgw.nix_input_observation import LOCK_NAR, NIX, REV, UNSHARE, NixInputObservationError, observe_archive
from tgw.nix_input_observation import TOOLS as TOOL_PATHS

DIGEST = "a" * 64
TOOLS = {name: "sha256:" + DIGEST for name in ("unshare", "ip", "python", "nix", "nix_store", "git")}


def request(archive, helper=b"# fixed standalone helper"):
    return {
        "source_archive_sha256": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
        "source_commit": "b" * 40,
        "source_tree": "c" * 40,
        "flake_lock_sha256": "sha256:" + "d" * 64,
        "module_sha256": "sha256:" + "e" * 64,
        "observer_source_sha256": "sha256:" + hashlib.sha256(helper).hexdigest(),
    }


def receipt(bound):
    value = {
        "schema": "tgw-nix-input-observation/v2",
        "request": bound,
        "namespace": {
            "start_inode": 42,
            "end_inode": 42,
            "loopback": "down",
            "other_links": [],
            "routes": [],
            "link_json": [{"ifname": "lo", "operstate": "DOWN", "flags": ["LOOPBACK"]}],
            "route_json": [],
            "link_json_sha256": "sha256:" + DIGEST,
            "route_json_sha256": "sha256:" + DIGEST,
            "held_for_entire_run": True,
        },
        "process": {"pid": 123, "starttime": 456, "exe_sha256": TOOLS["python"]},
        "tools": TOOLS,
        "negative_probes_before": {"dns": "denied", "public_https": "denied", "private": "denied", "metadata": "denied"},
        "negative_probes_after": {"dns": "denied", "public_https": "denied", "private": "denied", "metadata": "denied"},
        "lock_nodes": [{"node": "nixpkgs", "rev": REV, "nar_hash": LOCK_NAR}],
        "forced_inputs": [{"lock_node": "nixpkgs", "lock_rev": REV, "lock_nar_hash": LOCK_NAR, "path": "/nix/store/11111111111111111111111111111111-source", "nar_sha256": "sha256:" + DIGEST}],
        "evaluated_drv": "/nix/store/22222222222222222222222222222222-review.drv",
        "observed_outputs": [{"role": "derivation", "path": "/nix/store/22222222222222222222222222222222-review.drv", "nar_sha256": "sha256:" + DIGEST}],
        "cleanup": "removed",
        "nix_version": "nix (Nix) 2.28.5",
    }
    value["namespace"]["link_json_sha256"] = "sha256:" + hashlib.sha256(json.dumps(value["namespace"]["link_json"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    value["namespace"]["route_json_sha256"] = "sha256:" + hashlib.sha256(json.dumps(value["namespace"]["route_json"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    value["receipt_sha256"] = "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def test_one_helper_owns_archive_namespace_and_all_nix_steps(tmp_path):
    archive = tmp_path / "source.tar"
    archive.write_bytes(b"exact archive")
    req = request(archive)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        raw = kwargs["input"]
        frame_size = int.from_bytes(raw[:4], "big")
        offset = 4 + frame_size
        helper_size = int.from_bytes(raw[offset : offset + 8], "big")
        offset += 8 + helper_size + 64
        frame = json.loads(raw[4 : 4 + frame_size])
        bound = json.loads(raw[offset : offset + frame["payload_length"]])
        return subprocess.CompletedProcess(command, 0, json.dumps(receipt(bound)).encode(), b"")

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

    def run(command, **kwargs):
        raw = kwargs["input"]
        n = int.from_bytes(raw[:4], "big")
        off = 4 + n
        h = int.from_bytes(raw[off : off + 8], "big")
        off += 8 + h + 64
        frame = json.loads(raw[4 : 4 + n])
        bound = json.loads(raw[off : off + frame["payload_length"]])
        value = receipt(bound)
        value["namespace"]["routes"] = ["default"]
        return subprocess.CompletedProcess(command, 0, json.dumps(value).encode(), b"")

    with pytest.raises(NixInputObservationError, match="namespace"):
        observe_archive(archive, request=req, known_tool_sha256=TOOLS, helper_source=b"helper", run=run)


def test_flake_native_input_resolution_has_no_unlocked_getflake():
    source = open("src/tgw/nixos_reviewed_evaluation.py").read()
    assert ".#inputIdentities.nixpkgs.outPath" in source and "builtins.getFlake" not in source


@pytest.mark.parametrize("mutation", ["truncated", "bad_json", "bad_hash", "oversized"])
def test_real_bootstrap_frame_rejects_malformed_payload_with_bound_failure(tmp_path, mutation, monkeypatch):
    from tgw import nix_input_observation as module

    archive = tmp_path / "source.tar"
    archive.write_bytes(b"archive")
    req = request(archive, Path(module.__file__).read_bytes())
    bound = {**req, "tool_sha256": TOOLS, "tool_paths": TOOL_PATHS}
    raw = bytearray(module.packet(Path(module.__file__).read_bytes(), bound, archive))
    frame_size = int.from_bytes(raw[:4], "big")
    frame = json.loads(raw[4 : 4 + frame_size])
    helper_offset = 4 + frame_size
    helper_size = int.from_bytes(raw[helper_offset : helper_offset + 8], "big")
    payload_offset = helper_offset + 8 + helper_size + 64
    if mutation == "truncated":
        raw = raw[: payload_offset + frame["payload_length"] - 1]
    elif mutation == "bad_json":
        raw[payload_offset] = ord("x")
    elif mutation == "bad_hash":
        frame["payload_sha256"] = "sha256:" + "0" * 64
        encoded = json.dumps(frame, sort_keys=True, separators=(",", ":")).encode()
        assert len(encoded) == frame_size
        raw[4 : 4 + frame_size] = encoded
    else:
        raw[:4] = (4097).to_bytes(4, "big")
    monkeypatch.setattr("sys.stdin", type("Input", (), {"buffer": __import__("io").BytesIO(bytes(raw))})())
    output = __import__("io").StringIO()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path / "scratch"))
    (tmp_path / "scratch").mkdir()
    code = module.standalone_main()
    failure = json.loads(output.getvalue())
    assert code in {1, 2}
    assert failure["schema"] == "tgw-nix-input-observation-bootstrap-failure/v1"
    expected = frame if mutation != "oversized" else {}
    assert failure["request_sha256"] == expected.get("request_sha256", "unknown")
    assert failure["helper_sha256"] == expected.get("helper_sha256", "unknown")
    assert failure["tool_manifest_sha256"] == expected.get("tool_manifest_sha256", "unknown")
    assert failure["cleanup"] == "removed"
