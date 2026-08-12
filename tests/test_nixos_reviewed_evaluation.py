import io
import json
import struct
import subprocess
import tarfile
from hashlib import sha256
from pathlib import Path

import pytest

from tgw.nixos_reviewed_evaluation import EXECUTABLES, SSH_COMMAND, EvaluationError, SshReviewedEvaluationProvider, execute_packet

COMMIT = "a" * 40
TREE = "b" * 40
DIGEST = "c" * 64


def parameters(archive_digest=DIGEST):
    return {
        "target_host": "tgw-prod", "flake_repository_id": "tgw-flake",
        "artifact_ref": f"artifact:sha256:{archive_digest}", "source_commit": COMMIT,
        "source_tree": TREE, "source_archive_sha256": archive_digest,
        "flake_lock_sha256": DIGEST, "module_path": "nix/review-egress.nix", "module_sha256": DIGEST, "provider_sha256": DIGEST,
        "scratch_id": "nixos-review:test", "system": "x86_64-linux",
        "evaluation_target": "review-egress-systemd-units",
        "unit_set": "tgw-review-egress@.service,tgw-review-egress-attest@.service,tgw-review-egress-namespace@.service",
        "output_schema": "tgw-nixos-reviewed-evaluation-receipt/v1", "nix_network_policy": "offline-no-substituters",
        "minimum_systemd_version": "257", "max_duration_seconds": "300", "max_output_bytes": "1048576",
        "activate": "false", "profile_write": "false", "home_db_write": "false",
        "operation_id": "nixos-review:test", "generation": "eval-1",
    }


def make_archive(path: Path, *, commit=COMMIT):
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}) as archive:
        for name, data in (("flake.lock", b"lock"), ("nix/review-egress.nix", b"module")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def packet(request, archive):
    raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    return io.BytesIO(struct.pack("!Q", len(raw)) + raw + archive.read_bytes())


def test_controller_provider_uses_only_fixed_ssh_helper_and_content_bound_input(tmp_path):
    archive = tmp_path / "source.tar"
    make_archive(archive)
    digest = sha256(archive.read_bytes()).hexdigest()
    request = parameters(digest)
    output = json.dumps({"schema": "receipt"}).encode()
    def invoke(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, output, b"")

    provider = SshReviewedEvaluationProvider(lambda identity: archive, invoke=invoke)

    assert provider(request) == {"schema": "receipt"}
    assert SSH_COMMAND == (
        "ssh", "-oBatchMode=yes", "-oClearAllForwardings=yes", "-oStrictHostKeyChecking=yes",
        "--", "tgw-prod", "sudo", "-n", "--", "/run/current-system/sw/bin/tgw-nixos-reviewed-evaluation",
    )


def test_controller_provider_rejects_artifact_mismatch_before_ssh(tmp_path):
    archive = tmp_path / "source.tar"
    archive.write_bytes(b"wrong")
    called = []
    with pytest.raises(EvaluationError, match="digest mismatch"):
        SshReviewedEvaluationProvider(lambda _: archive, invoke=lambda *a, **k: called.append(a))(parameters())
    assert not called


def test_remote_helper_executes_only_fixed_offline_steps_and_cleans_scratch(tmp_path, monkeypatch):
    archive = tmp_path / "source.tar"
    make_archive(archive)
    digest = sha256(archive.read_bytes()).hexdigest()
    request = parameters(digest)
    request["flake_lock_sha256"] = "sha256:" + sha256(b"lock").hexdigest()
    request["module_sha256"] = "sha256:" + sha256(b"module").hexdigest()
    import tgw.nixos_reviewed_evaluation as provider_module

    request["provider_sha256"] = "sha256:" + sha256(Path(provider_module.__file__).read_bytes()).hexdigest()
    closure = "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-nixos-system-tgw-prod-test"
    original_is_file = Path.is_file
    original_digest = __import__("tgw.nixos_reviewed_evaluation", fromlist=["_digest_file"])._digest_file
    monkeypatch.setattr(Path, "is_file", lambda path: True if str(path).startswith(closure + "/etc/systemd/system/") else original_is_file(path))
    monkeypatch.setattr("tgw.nixos_reviewed_evaluation._digest_file", lambda path: "sha256:" + DIGEST if str(path).startswith(closure) else original_digest(path))
    calls = []

    def fake_run(argv, *, cwd, timeout):
        calls.append(argv)
        if argv[:2] == [EXECUTABLES["git"], "write-tree"]:
            return TREE
        if "drvPath" in argv[-1]:
            return "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-review.drv"
        if "build" in argv:
            return closure
        if argv == [EXECUTABLES["systemd_analyze"], "--version"]:
            return "systemd 257\n"
        if argv == [EXECUTABLES["nix"], "--version"]:
            return "nix (Nix) 2.28.5\n"
        return ""

    scratch = tmp_path / "scratch"
    result = execute_packet(packet(request, archive), run=fake_run, scratch_root=scratch)

    assert result["cleanup"] == "removed" and not list(scratch.iterdir())
    nix_calls = [call for call in calls if call[0] == EXECUTABLES["nix"] and call != [EXECUTABLES["nix"], "--version"]]
    assert nix_calls and all(call[1:6] == ["--offline", "--option", "substituters", "", "--no-write-lock-file"] for call in nix_calls)
    assert not any(word in {"switch", "boot", "test", "profile"} for call in calls for word in call)
    assert set(result["unit_sha256"]) == set(request["unit_set"].split(","))


@pytest.mark.parametrize("change", [{"activate": "true"}, {"module_path": "../../etc/passwd"}, {"command": "id"}])
def test_remote_helper_rejects_broadened_request_before_scratch(tmp_path, change):
    archive = tmp_path / "source.tar"
    make_archive(archive)
    request = {**parameters(sha256(archive.read_bytes()).hexdigest()), **change}
    scratch = tmp_path / "scratch"
    with pytest.raises(ValueError):
        execute_packet(packet(request, archive), scratch_root=scratch)
    assert not scratch.exists()


def test_remote_helper_cleans_scratch_on_failure(tmp_path):
    archive = tmp_path / "source.tar"
    make_archive(archive, commit="d" * 40)
    request = parameters(sha256(archive.read_bytes()).hexdigest())
    import tgw.nixos_reviewed_evaluation as provider_module

    request["provider_sha256"] = "sha256:" + sha256(Path(provider_module.__file__).read_bytes()).hexdigest()
    scratch = tmp_path / "scratch"
    with pytest.raises(EvaluationError, match="commit identity"):
        execute_packet(packet(request, archive), scratch_root=scratch)
    assert not list(scratch.iterdir())


def test_provider_source_has_no_shell_or_activation_escape_hatch():
    import tgw.nixos_reviewed_evaluation as provider_module

    source = Path(provider_module.__file__).read_text()
    assert "shell=True" not in source
    assert "nixos-rebuild" not in source
    assert "nix profile" not in source
    assert "/home/db/tgw-flake" not in source
    assert "SSH_COMMAND = (" in source
