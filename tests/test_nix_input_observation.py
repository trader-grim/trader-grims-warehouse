import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tgw.nix_input_observation import LOCK_NAR, NIX, PREFIX, REV, UNSHARE, NixInputObservationError, observe_archive
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
        _, _, frame_size, helper_size, payload_size, *_ = PREFIX.unpack(raw[: PREFIX.size])
        offset = PREFIX.size + frame_size + helper_size
        bound = json.loads(raw[offset : offset + payload_size])
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
        _, _, n, h, payload_size, *_ = PREFIX.unpack(raw[: PREFIX.size])
        off = PREFIX.size + n + h
        bound = json.loads(raw[off : off + payload_size])
        value = receipt(bound)
        value["namespace"]["routes"] = ["default"]
        return subprocess.CompletedProcess(command, 0, json.dumps(value).encode(), b"")

    with pytest.raises(NixInputObservationError, match="namespace"):
        observe_archive(archive, request=req, known_tool_sha256=TOOLS, helper_source=b"helper", run=run)


def test_flake_native_input_resolution_has_no_unlocked_getflake():
    source = open("src/tgw/nixos_reviewed_evaluation.py").read()
    assert ".#inputIdentities.nixpkgs.outPath" in source and "builtins.getFlake" not in source


@pytest.mark.parametrize("mutation", ["truncated", "bad_json", "bad_hash", "bad_magic"])
def test_real_bootstrap_frame_rejects_malformed_payload_with_bound_failure(tmp_path, mutation):
    from tgw import nix_input_observation as module

    archive = tmp_path / "source.tar"
    archive.write_bytes(b"archive")
    req = request(archive, Path(module.__file__).read_bytes())
    bound = {**req, "tool_sha256": TOOLS, "tool_paths": TOOL_PATHS}
    raw = bytearray(module.packet(Path(module.__file__).read_bytes(), bound, archive))
    magic, version, frame_size, helper_size, payload_size, request_hash, helper_hash, tool_hash = PREFIX.unpack(raw[: PREFIX.size])
    payload_offset = PREFIX.size + frame_size + helper_size
    if mutation == "truncated":
        raw = raw[: payload_offset + payload_size - 1]
    elif mutation == "bad_json":
        raw[payload_offset] = ord("x")
    elif mutation == "bad_hash":
        raw[payload_offset] ^= 1
    else:
        raw[:8] = b"BADMAGIC"
    completed = subprocess.run([__import__("sys").executable, "-I", "-c", module.BOOTSTRAP], input=bytes(raw), capture_output=True, check=False)
    failure = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert failure["schema"] == "tgw-nix-input-observation-bootstrap-failure/v1"
    assert failure["request_sha256"] == "sha256:" + request_hash.hex()
    assert failure["helper_sha256"] == "sha256:" + helper_hash.hex()
    assert failure["tool_manifest_sha256"] == "sha256:" + tool_hash.hex()
    assert failure["cleanup"] == "removed"


def test_real_bootstrap_executes_inert_helper_with_exact_reconstructed_payload_and_tail(tmp_path):
    from tgw import nix_input_observation as module

    archive = tmp_path / "source.tar"
    archive.write_bytes(b"exact-tail")
    helper = b"""import hashlib,json,struct,sys
n=struct.unpack('!Q',sys.stdin.buffer.read(8))[0]; payload=sys.stdin.buffer.read(n); tail=sys.stdin.buffer.read()
q=json.loads(payload); print(json.dumps({'request':hashlib.sha256(payload).hexdigest(),'tail':hashlib.sha256(tail).hexdigest(),'commit':q['source_commit']},sort_keys=True,separators=(',',':')))
"""
    req = request(archive, helper)
    bound = {**req, "tool_sha256": TOOLS, "tool_paths": TOOL_PATHS}
    wire = module.packet(helper, bound, archive)
    completed = subprocess.run([__import__("sys").executable, "-I", "-c", module.BOOTSTRAP], input=wire, capture_output=True, check=False)
    assert completed.returncode == 0
    marker = json.loads(completed.stdout)
    payload = json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()
    assert marker == {
        "request": hashlib.sha256(payload).hexdigest(),
        "tail": hashlib.sha256(b"exact-tail").hexdigest(),
        "commit": req["source_commit"],
    }


@pytest.mark.parametrize("cleanup", ["removed", "ambiguous"])
def test_controller_validates_actual_bootstrap_to_inert_helper_failure(tmp_path, cleanup):
    from tgw import nix_input_observation as module

    archive = tmp_path / "source.tar"
    archive.write_bytes(b"tail")
    helper = f"""import hashlib,json,os,struct,sys
n=struct.unpack('!Q',sys.stdin.buffer.read(8))[0]; q=json.loads(sys.stdin.buffer.read(n)); sys.stdin.buffer.read()
canon=lambda x: json.dumps(x,sort_keys=True,separators=(',',':')).encode()
d={{'schema':'tgw-nix-input-observation-failure/v1','request_sha256':'sha256:'+hashlib.sha256(canon(q)).hexdigest()}}
d.update({{'helper_sha256':q['observer_source_sha256'],'tool_manifest_sha256':'sha256:'+hashlib.sha256(canon(q['tool_sha256'])).hexdigest()}})
d.update({{'stage':'nix','code':'NixInputObservationError','outcome':'{"FAILED" if cleanup == "removed" else "AMBIGUOUS"}','cleanup':'{cleanup}','netns_inode':os.stat('/proc/self/ns/net').st_ino}})
d['receipt_sha256']='sha256:'+hashlib.sha256(canon(d)).hexdigest(); print(canon(d).decode()); raise SystemExit({1 if cleanup == "removed" else 2})
""".encode()
    req = request(archive, helper)

    def run(command, **kwargs):
        return subprocess.run([__import__("sys").executable, "-I", "-c", module.BOOTSTRAP], input=kwargs["input"], capture_output=True, check=False)

    with pytest.raises(NixInputObservationError, match="validated failure"):
        observe_archive(archive, request=req, known_tool_sha256=TOOLS, helper_source=helper, run=run)
