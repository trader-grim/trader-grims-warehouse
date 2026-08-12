import hashlib
import json
import subprocess

import pytest

from tgw.nix_input_observation import LOCK_NAR, NIX, REV, UNSHARE, NixInputObservationError, observe, validate_receipt

SOURCE_COMMIT = "a" * 40
SOURCE_TREE = "b" * 40
LOCK_SHA = "sha256:" + "c" * 64
INPUT = "/nix/store/11111111111111111111111111111111-source"
DRV = "/nix/store/22222222222222222222222222222222-review.drv"
NAR = "d" * 64


def runner(*, additions=(), metadata_mutation=None):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        tail = command[command.index(NIX) + 1 :]
        if "metadata" in tail:
            value = {"locks": {"nodes": {"root": {"inputs": {"nixpkgs": "nixpkgs"}}, "nixpkgs": {"locked": {"rev": REV, "narHash": LOCK_NAR}}}}}
            if metadata_mutation:
                metadata_mutation(value)
            output = json.dumps(value)
        elif tail[-3:] == ["path-info", "--all", "--json"]:
            output = json.dumps([INPUT, *additions] if sum("path-info" in item[0] for item in calls) > 1 else [INPUT])
        elif tail[-1] == ".#inputIdentities.nixpkgs.outPath":
            output = INPUT
        elif "hash" in tail:
            output = NAR
        elif tail[-1].endswith(".drvPath"):
            output = DRV
        else:
            raise AssertionError(tail)
        return subprocess.CompletedProcess(command, 0, output, "")

    return run, calls


def test_zero_fetch_observation_uses_fixed_nix_228_command_contract(tmp_path):
    run, calls = runner()
    receipt = observe(tmp_path, source_commit=SOURCE_COMMIT, source_tree=SOURCE_TREE, flake_lock_sha256=LOCK_SHA, run=run)
    assert receipt["forced_inputs"][0]["path"] == INPUT
    assert receipt["store_additions"] == []
    assert len(calls) == 6
    for command, kwargs in calls:
        assert command[:4] == [UNSHARE, "--net", "--map-root-user", "--"]
        assert "NIX_REMOTE=local" in command
        assert ["--offline", "--option", "substituters", ""] == command[command.index(NIX) + 1 : command.index(NIX) + 5]
        assert "--no-write-lock-file" in command and "--impure" not in command
        assert kwargs["timeout"] == 120 and kwargs["capture_output"] is True


def test_observation_rejects_store_addition_and_extra_lock_node(tmp_path):
    run, _ = runner(additions=["/nix/store/33333333333333333333333333333333-added"])
    with pytest.raises(NixInputObservationError, match="wrote to the store"):
        observe(tmp_path, source_commit=SOURCE_COMMIT, source_tree=SOURCE_TREE, flake_lock_sha256=LOCK_SHA, run=run)

    def add_node(value):
        value["locks"]["nodes"]["unrelated"] = {"locked": {}}

    run, _ = runner(metadata_mutation=add_node)
    with pytest.raises(NixInputObservationError, match="lock graph"):
        observe(tmp_path, source_commit=SOURCE_COMMIT, source_tree=SOURCE_TREE, flake_lock_sha256=LOCK_SHA, run=run)


def test_observation_receipt_rejects_tampering(tmp_path):
    run, _ = runner()
    receipt = observe(tmp_path, source_commit=SOURCE_COMMIT, source_tree=SOURCE_TREE, flake_lock_sha256=LOCK_SHA, run=run)
    receipt["forced_inputs"][0]["path"] = "/nix/store/33333333333333333333333333333333-other"
    with pytest.raises(NixInputObservationError, match="self-hash"):
        validate_receipt(receipt, source_commit=SOURCE_COMMIT, source_tree=SOURCE_TREE, flake_lock_sha256=LOCK_SHA)


def test_flake_native_input_resolution_has_no_unlocked_getflake():
    source = open("src/tgw/nixos_reviewed_evaluation.py").read()
    assert ".#inputIdentities.nixpkgs.outPath" in source
    assert "builtins.getFlake" not in source
    flake = open("flake.nix").read()
    assert "inputIdentities.nixpkgs" in flake
    assert 'rev = "' + REV + '"' in flake
    assert hashlib.sha256(open("flake.lock", "rb").read()).hexdigest()
