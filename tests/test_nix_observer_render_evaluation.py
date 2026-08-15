import hashlib
import json
import shutil
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tgw.nix_observer_render_evaluation import OUTPUTS, RESULT_SCHEMA, SCHEMA, RenderEvaluationError, canonical, produce_result, validate_request, validate_result


def request():
    value = {
        "schema": SCHEMA,
        "plan_commit": "a" * 40,
        "source_commit": "b" * 40,
        "source_tree": "c" * 40,
        "artifact_ref": "artifact:sha256:" + "d" * 64,
        "archive_sha256": "sha256:" + "d" * 64,
        "flake_lock_sha256": "sha256:" + "e" * 64,
        "flake_sha256": "sha256:" + "f" * 64,
        "module_sha256": "sha256:" + "1" * 64,
        "launcher_source_sha256": "sha256:" + "2" * 64,
        "observer_source_sha256": "sha256:" + "3" * 64,
        "provider_sha256": "sha256:" + "4" * 64,
        "host_identity_receipt_sha256": "sha256:" + "5" * 64,
        "systemd_analyze_sha256": "sha256:" + "6" * 64,
        "systemd_analyze_version_stdout_sha256": "sha256:" + hashlib.sha256(b"systemd 257 (257.10)\nfeatures\n").hexdigest(),
        "target": "nix-input-observer-rendered-artifacts",
        "system": "x86_64-linux",
        "network_policy": "offline-no-substituters",
        "allow_ifd": False,
        "activate": False,
        "profile_write": False,
        "home_db_write": False,
        "expected_outputs": list(OUTPUTS),
        "expected_metadata_status": "NON_DEPLOYABLE_RENDER_FIXTURE",
        "input_closure_manifest": [
            {
                "node": "nixpkgs",
                "rev": "ac62194c3917d5f474c1a844b6fd6da2db95077d",
                "lock_nar_hash": "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w=",
                "store_path": "/nix/store/11111111111111111111111111111111-source",
                "nar_sha256": "sha256:" + "7" * 64,
            }
        ],
        "input_closure_path_count": 1,
        "systemd_analyze_version": "systemd 257 (257.10)",
        "systemd_analyze_version_stdout_bytes": len(b"systemd 257 (257.10)\nfeatures\n"),
        "max_duration_seconds": 900,
        "max_output_bytes": 16 * 1024 * 1024,
    }
    value["input_closure_manifest_sha256"] = "sha256:" + hashlib.sha256(canonical(value["input_closure_manifest"])).hexdigest()
    value["request_sha256"] = "sha256:" + hashlib.sha256(canonical(value)).hexdigest()
    return value


def test_closed_non_deployable_render_request():
    assert validate_request(request())["expected_outputs"] == list(OUTPUTS)


@pytest.mark.parametrize("field,value", [("allow_ifd", True), ("activate", True), ("expected_metadata_status", "DEPLOYABLE"), ("target", "review-egress-systemd-units")])
def test_render_request_mutations_fail_closed(field, value):
    item = request()
    item[field] = value
    unsigned = dict(item)
    unsigned.pop("request_sha256")
    item["request_sha256"] = "sha256:" + hashlib.sha256(canonical(unsigned)).hexdigest()
    with pytest.raises(RenderEvaluationError):
        validate_request(item)


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


def result(req=None):
    req = req or request()
    files = [{"path": path, "sha256": "sha256:" + hashlib.sha256(path.encode()).hexdigest(), "size": index + 1} for index, path in enumerate(OUTPUTS)]
    units = [item for item in files if item["path"] in OUTPUTS[9:12]]
    value = {
        "schema": RESULT_SCHEMA,
        "request_sha256": req["request_sha256"],
        "outcome": "VERIFIED",
        "metadata_status": "NON_DEPLOYABLE_RENDER_FIXTURE",
        "files": files,
        "output_root": "/nix/store/22222222222222222222222222222222-render",
        "evaluated_drv": "/nix/store/33333333333333333333333333333333-render.drv",
        "drv_output": {"drv": "/nix/store/33333333333333333333333333333333-render.drv", "output": "/nix/store/22222222222222222222222222222222-render"},
        "output_manifest_sha256": "sha256:" + hashlib.sha256(canonical(files)).hexdigest(),
        "systemd_verify": {
            "executable_sha256": req["systemd_analyze_sha256"],
            "version": req["systemd_analyze_version"],
            "argv": ["systemd-analyze", "verify", *OUTPUTS[9:12]],
            "exit_code": 0,
            "stdout_bytes": 0,
            "stdout_sha256": "sha256:" + "0" * 64,
            "stderr_bytes": 0,
            "stderr_sha256": "sha256:" + "0" * 64,
            "units_sha256": "sha256:" + hashlib.sha256(canonical(units)).hexdigest(),
            "observed_at": "2026-08-12T12:00:00Z",
            "host_identity_receipt_sha256": req["host_identity_receipt_sha256"],
        },
        "cleanup": "removed",
        "effects": {"build": True, "activation": False, "deployment": False, "profile_write": False, "home_db_write": False, "live_flake_write": False, "network": False},
    }
    value["receipt_sha256"] = "sha256:" + hashlib.sha256(canonical(value)).hexdigest()
    return value


def rehash(value):
    value = deepcopy(value)
    value.pop("receipt_sha256", None)
    value["receipt_sha256"] = "sha256:" + hashlib.sha256(canonical(value)).hexdigest()
    return value


def test_complete_valid_result():
    req = request()
    assert validate_result(result(req), request=req, now=NOW)["outcome"] == "VERIFIED"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda x: x.update(receipt_sha256="sha256:" + "0" * 64),
        lambda x: x.update(request_sha256="sha256:" + "0" * 64),
        lambda x: x.update(metadata_status="DEPLOYABLE"),
        lambda x: x.update(cleanup="retained"),
        lambda x: x.update(output_root="/tmp/out"),
        lambda x: x.update(evaluated_drv="/nix/store/33333333333333333333333333333333-render"),
        lambda x: x["drv_output"].update(output="/nix/store/44444444444444444444444444444444-other"),
        lambda x: x["files"].reverse(),
        lambda x: x["files"][0].update(sha256="bad"),
        lambda x: x.update(output_manifest_sha256="sha256:" + "0" * 64),
        lambda x: x["systemd_verify"].update(units_sha256="sha256:" + "0" * 64),
        lambda x: x["systemd_verify"].update(argv=["systemd-analyze", "verify"]),
        lambda x: x["systemd_verify"].update(exit_code=1),
        lambda x: x["systemd_verify"].update(stdout_bytes=20_000_000),
        lambda x: x["systemd_verify"].update(executable_sha256="sha256:" + "0" * 64),
        lambda x: x["systemd_verify"].update(host_identity_receipt_sha256="sha256:" + "0" * 64),
        lambda x: x["systemd_verify"].update(observed_at="2020-01-01T00:00:00Z"),
        lambda x: x["effects"].update(network=True),
        lambda x: x["effects"].update(build=False),
    ],
)
def test_result_mutations_fail_closed(mutate):
    req = request()
    value = result(req)
    mutate(value)
    if value["receipt_sha256"] != "sha256:" + "0" * 64:
        value = rehash(value)
    with pytest.raises(RenderEvaluationError):
        validate_result(value, request=req, now=NOW)


def provider_case(tmp_path, monkeypatch, *, metadata_mutation=None, run_mutation=None):
    from tgw import nix_observer_render_evaluation as module

    req = request()
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(module, "STORE_ROOT", store)
    root = store / "22222222222222222222222222222222-render"
    for name in OUTPUTS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    metadata_files = [{"path": name, "sha256": "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()} for name in OUTPUTS[:-1]]
    (root / "verifier-metadata.json").write_text(
        json.dumps(
            {
                "schema": "tgw-nix-input-observer-render/v1",
                "system": "x86_64-linux",
                "descriptor_status": "NON_DEPLOYABLE_RENDER_FIXTURE",
                "activation": False,
                "units": list(OUTPUTS[9:12]),
                "files": metadata_files,
            }
        )
    )
    if metadata_mutation:
        value = json.loads((root / "verifier-metadata.json").read_text())
        metadata_mutation(value)
        (root / "verifier-metadata.json").write_text(json.dumps(value))
    nix = tmp_path / "nix"
    verifier = tmp_path / "systemd-analyze"
    nix.write_bytes(b"nix")
    verifier.write_bytes(b"verify")
    nix.chmod(0o500)
    verifier.chmod(0o500)
    req["systemd_analyze_sha256"] = "sha256:" + hashlib.sha256(b"verify").hexdigest()
    unsigned = dict(req)
    unsigned.pop("request_sha256")
    req["request_sha256"] = "sha256:" + hashlib.sha256(canonical(unsigned)).hexdigest()
    drv = "/nix/store/33333333333333333333333333333333-render.drv"
    out = str(root)
    scratch = tmp_path / "scratch-parent"
    scratch.mkdir(mode=0o700)
    monkeypatch.setattr(module, "SCRATCH_PARENT", scratch)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if run_mutation:
            changed = run_mutation(argv, kwargs, scratch, root, drv)
            if changed is not None:
                return changed
        if argv[1:3] == ["derivation", "show"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({drv: {"outputs": {"out": {"path": out}}}}), "")
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, b"systemd 257 (257.10)\nfeatures\n", b"")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    kwargs = {
        "request": req,
        "output_root": root,
        "evaluated_drv": drv,
        "nix": nix,
        "nix_sha256": "sha256:" + hashlib.sha256(b"nix").hexdigest(),
        "systemd_analyze": verifier,
        "now": NOW,
        "run": run,
    }
    return kwargs, calls, scratch, root


def test_provider_derives_receipt_from_held_files_and_actual_commands(tmp_path, monkeypatch):
    kwargs, calls, scratch, _ = provider_case(tmp_path, monkeypatch)
    receipt = produce_result(**kwargs)
    assert receipt["effects"]["build"] is True
    assert calls[0][1]["pass_fds"] and len(calls[2][1]["pass_fds"]) == 1
    assert not list(scratch.iterdir())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra=True),
        lambda value: value.update(system="aarch64-linux"),
        lambda value: value["files"][0].update(sha256="sha256:" + "0" * 64),
        lambda value: value["units"].reverse(),
    ],
)
def test_provider_rejects_metadata_lies(tmp_path, monkeypatch, mutation):
    kwargs, _, scratch, _ = provider_case(tmp_path, monkeypatch, metadata_mutation=mutation)
    with pytest.raises(RenderEvaluationError, match="metadata contract"):
        produce_result(**kwargs)
    assert not list(scratch.iterdir())


@pytest.mark.parametrize("version", [b"systemd 256\n", b"systemd 257 (257.10)\nextra", b"x" * 4097])
def test_provider_rejects_malformed_or_oversize_verifier_version(tmp_path, monkeypatch, version):
    def mutate(argv, _kwargs, _scratch, _root, _drv):
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, version, b"")

    kwargs, _, scratch, _ = provider_case(tmp_path, monkeypatch, run_mutation=mutate)
    with pytest.raises(RenderEvaluationError, match="verifier version"):
        produce_result(**kwargs)
    assert not list(scratch.iterdir())


def test_provider_rejects_output_identity_substitution(tmp_path, monkeypatch):
    def mutate(argv, _kwargs, _scratch, _root, drv):
        if argv[1:3] == ["derivation", "show"]:
            other = "/nix/store/44444444444444444444444444444444-other"
            return subprocess.CompletedProcess(argv, 0, json.dumps({drv: {"outputs": {"out": {"path": other}}}}), "")

    kwargs, _, scratch, _ = provider_case(tmp_path, monkeypatch, run_mutation=mutate)
    with pytest.raises(RenderEvaluationError, match="output observation"):
        produce_result(**kwargs)
    assert not list(scratch.iterdir())


def test_provider_rejects_output_file_symlink(tmp_path, monkeypatch):
    kwargs, _, scratch, root = provider_case(tmp_path, monkeypatch)
    victim = root / OUTPUTS[0]
    victim.unlink()
    victim.symlink_to(root / OUTPUTS[1])
    with pytest.raises(OSError):
        produce_result(**kwargs)
    assert not list(scratch.iterdir())


def test_provider_cleanup_uncertainty_overrides_success(tmp_path, monkeypatch):
    touched = False

    def mutate(argv, _kwargs, scratch, _root, _drv):
        nonlocal touched
        if argv[1:] == ["--version"] and not touched:
            attempt = next(scratch.iterdir())
            (attempt / "hostile").write_text("not provider-owned")
            touched = True

    kwargs, _, scratch, _ = provider_case(tmp_path, monkeypatch, run_mutation=mutate)
    with pytest.raises(RenderEvaluationError, match="cleanup ambiguous"):
        produce_result(**kwargs)
    assert (next(scratch.iterdir()) / "hostile").exists()


def test_provider_cleans_materialized_units_after_verifier_failure(tmp_path, monkeypatch):
    def mutate(argv, _kwargs, _scratch, _root, _drv):
        if len(argv) > 1 and argv[1] == "verify":
            assert all(Path(path).name == expected.removeprefix("units/") for path, expected in zip(argv[2:], OUTPUTS[9:12], strict=True))
            return subprocess.CompletedProcess(argv, 1, b"", b"bounded failure")

    kwargs, _, scratch, _ = provider_case(tmp_path, monkeypatch, run_mutation=mutate)
    with pytest.raises(RenderEvaluationError, match="verification failed"):
        produce_result(**kwargs)
    assert not list(scratch.iterdir())


def test_provider_rejects_materialized_unit_path_swap(tmp_path, monkeypatch):
    swapped = False

    def mutate(argv, _kwargs, _scratch, _root, _drv):
        nonlocal swapped
        if len(argv) > 1 and argv[1] == "verify" and not swapped:
            victim = Path(argv[2])
            victim.unlink()
            victim.symlink_to("/dev/null")
            swapped = True

    kwargs, _, scratch, _ = provider_case(tmp_path, monkeypatch, run_mutation=mutate)
    with pytest.raises(RenderEvaluationError, match="identity changed"):
        produce_result(**kwargs)
    assert not list(scratch.iterdir())


def test_provider_rejects_untrusted_or_symlink_scratch_parent(tmp_path, monkeypatch):
    from tgw import nix_observer_render_evaluation as module

    kwargs, _, scratch, _ = provider_case(tmp_path, monkeypatch)
    scratch.chmod(0o755)
    with pytest.raises(RenderEvaluationError, match="parent trust"):
        produce_result(**kwargs)
    scratch.chmod(0o700)
    target = tmp_path / "other"
    target.mkdir()
    scratch.rmdir()
    scratch.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(module, "SCRATCH_PARENT", scratch)
    with pytest.raises(OSError):
        produce_result(**kwargs)


def test_real_systemd_analyze_accepts_canonical_materialized_unit_names(tmp_path):
    verifier = shutil.which("systemd-analyze")
    if verifier is None:
        pytest.skip("systemd-analyze unavailable")
    launcher = tmp_path / "immutable-launcher"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o555)
    units = {
        OUTPUTS[9]: "[Unit]\nDescription=Fixed cgroup for bounded TGW Nix input observation\n[Slice]\nCPUQuota=100%\nMemoryMax=1G\nTasksMax=64\n",
        OUTPUTS[10]: "[Unit]\nDescription=Closed local TGW Nix observer transport\n[Socket]\nListenStream=%t/tgw-observer-test.sock\nSocketMode=0600\nAccept=yes\nMaxConnections=1\nRemoveOnStop=yes\n",
        OUTPUTS[11]: (
            "[Unit]\nDescription=One-shot fixed TGW Nix observer %i\n[Service]\nType=simple\n"
            f"ExecStart={launcher}\nStandardInput=socket\nStandardOutput=socket\nStandardError=journal\n"
            "Slice=tgw-nix-input-observer.slice\nUser=root\nGroup=root\nRuntimeMaxSec=180\nOOMPolicy=stop\n"
        ),
    }
    paths = []
    for name, body in units.items():
        path = tmp_path / name.removeprefix("units/")
        path.write_text(body)
        paths.append(str(path))
    version = subprocess.run([verifier, "--version"], capture_output=True, check=False, timeout=30)
    assert version.returncode == 0
    assert len(version.stdout.splitlines()) > 1
    verified = subprocess.run([verifier, "verify", *paths], capture_output=True, check=False, timeout=30)
    assert verified.returncode == 0, verified.stderr.decode(errors="replace")
    launcher.unlink()
    missing = subprocess.run([verifier, "verify", *paths], capture_output=True, check=False, timeout=30)
    assert missing.returncode != 0
