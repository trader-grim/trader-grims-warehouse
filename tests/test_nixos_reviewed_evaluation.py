import io
import json
import os
import socket
import struct
import subprocess
import tarfile
import time
from hashlib import sha256
from pathlib import Path

import pytest

from tgw.nixos_reviewed_evaluation import (
    EXECUTABLES,
    FAILURE_STAGES,
    SSH_EXECUTABLE,
    EvaluationError,
    RemoteEvaluationFailure,
    SshReviewedEvaluationProvider,
    _sealed_memfd,
    create_failure_receipt,
    execute_packet,
    validate_failure_receipt,
)

COMMIT = "a" * 40
TREE = "b" * 40
DIGEST = "c" * 64


def parameters(archive_digest=DIGEST):
    return {
        "target_host": "tgw-prod",
        "flake_repository_id": "tgw-flake",
        "artifact_ref": f"artifact:sha256:{archive_digest}",
        "source_commit": COMMIT,
        "source_tree": TREE,
        "source_archive_sha256": archive_digest,
        "archive_root": "trader-grims-warehouse",
        "flake_lock_sha256": DIGEST,
        "module_path": "nix/review-egress.nix",
        "module_sha256": DIGEST,
        "provider_sha256": DIGEST,
        "ssh_sha256": DIGEST,
        "known_hosts_sha256": DIGEST,
        "remote_python_sha256": DIGEST,
        "git_sha256": DIGEST,
        "nix_sha256": DIGEST,
        "nix_store_sha256": DIGEST,
        "systemd_analyze_sha256": DIGEST,
        "scratch_id": "nixos-review:test",
        "system": "x86_64-linux",
        "evaluation_target": "review-egress-systemd-units",
        "unit_set": "tgw-review-egress@.service,tgw-review-egress-attest@.service,tgw-review-egress-namespace@.service",
        "output_schema": "tgw-nixos-reviewed-evaluation-receipt/v1",
        "nix_network_policy": "offline-no-substituters",
        "minimum_systemd_version": "257",
        "max_duration_seconds": "300",
        "max_output_bytes": "1048576",
        "max_archive_bytes": "1048576",
        "max_unpacked_bytes": "4194304",
        "max_files": "1000",
        "activate": "false",
        "profile_write": "false",
        "home_db_write": "false",
        "operation_id": "nixos-review:test",
        "generation": "eval-1",
    }


def make_archive(path: Path, *, commit=COMMIT):
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}) as archive:
        root = "trader-grims-warehouse/"
        info = tarfile.TarInfo(root)
        info.type = tarfile.DIRTYPE
        archive.addfile(info)
        for name, data in ((root + "flake.lock", b"lock"), (root + "nix/review-egress.nix", b"module")):
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
    commands = []

    def invoke(argv, **kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, output, b"")

    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("100.107.99.66 ssh-ed25519 AAAA")
    known_hosts.chmod(0o600)
    request["ssh_sha256"] = "sha256:" + sha256(Path(SSH_EXECUTABLE).read_bytes()).hexdigest()
    request["known_hosts_sha256"] = "sha256:" + sha256(known_hosts.read_bytes()).hexdigest()
    provider = SshReviewedEvaluationProvider(lambda identity: archive, known_hosts=known_hosts, request_hash="sha256:" + "d" * 64, invoke=invoke)

    assert provider(request) == {"schema": "receipt"}
    assert SSH_EXECUTABLE == "/usr/bin/ssh"
    assert "-F" in commands[0] and "/dev/null" in commands[0]
    assert "codex@100.107.99.66" in commands[0] and "tgw-prod" not in commands[0]


@pytest.mark.parametrize("stage", sorted(FAILURE_STAGES))
def test_failure_receipt_all_stages_are_exact_and_self_hashed(stage):
    request = parameters()
    context = {
        "request_hash": "sha256:" + "d" * 64,
        "effect_hash": __import__("tgw.nixos_reviewed_evaluation", fromlist=["_effect_hash"])._effect_hash(request),
        "generation": request["generation"],
        "provider_sha256": request["provider_sha256"],
        "scratch_root_created": True,
        "run_created": True,
        "cleanup_attempted": True,
    }
    receipt = create_failure_receipt(
        context=context,
        stage=stage,
        diagnostic_code="SUBPROCESS_FAILED",
        exception_class="EvaluationError",
        cleanup_result="removed",
        subprocess_step="nix-build",
        return_code=1,
        stdout=b"private output",
        stderr=b"private error",
    )
    assert validate_failure_receipt(receipt, request, request_hash=context["request_hash"]) == receipt
    encoded = json.dumps(receipt)
    assert "private output" not in encoded and "private error" not in encoded


def test_failure_receipt_cleanup_ambiguity_and_fabrication_are_rejected():
    request = parameters()
    context = {
        "request_hash": "sha256:" + "d" * 64,
        "effect_hash": __import__("tgw.nixos_reviewed_evaluation", fromlist=["_effect_hash"])._effect_hash(request),
        "generation": request["generation"],
        "provider_sha256": request["provider_sha256"],
    }
    receipt = create_failure_receipt(
        context=context,
        stage="cleanup",
        diagnostic_code="CLEANUP_FAILED",
        exception_class="OSError",
        cleanup_result="failed",
    )
    assert receipt["outcome"] == "AMBIGUOUS"
    validate_failure_receipt(receipt, request, request_hash=context["request_hash"])
    for mutation in ({"outcome": "FAILED"}, {"stage": "shell"}, {"raw_stderr": "secret"}):
        forged = dict(receipt)
        forged.update(mutation)
        with pytest.raises(EvaluationError):
            validate_failure_receipt(forged, request, request_hash=context["request_hash"])


def test_controller_persists_only_validated_remote_failure(tmp_path):
    archive = tmp_path / "source.tar"
    make_archive(archive)
    request = parameters(sha256(archive.read_bytes()).hexdigest())
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("100.107.99.66 ssh-ed25519 AAAA")
    known_hosts.chmod(0o600)
    request["ssh_sha256"] = "sha256:" + sha256(Path(SSH_EXECUTABLE).read_bytes()).hexdigest()
    request["known_hosts_sha256"] = "sha256:" + sha256(known_hosts.read_bytes()).hexdigest()
    request_hash = "sha256:" + "d" * 64
    context = {
        "request_hash": request_hash,
        "effect_hash": __import__("tgw.nixos_reviewed_evaluation", fromlist=["_effect_hash"])._effect_hash(request),
        "generation": request["generation"],
        "provider_sha256": request["provider_sha256"],
        "cleanup_attempted": True,
    }
    failure = create_failure_receipt(
        context=context,
        stage="nix-eval",
        diagnostic_code="SUBPROCESS_FAILED",
        exception_class="EvaluationError",
        cleanup_result="removed",
        subprocess_step="nix-eval",
        return_code=1,
    )
    persisted = []
    provider = SshReviewedEvaluationProvider(
        lambda _: archive,
        known_hosts=known_hosts,
        request_hash=request_hash,
        failure_sink=persisted.append,
        invoke=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, json.dumps(failure).encode(), b"raw secret"),
    )
    with pytest.raises(RemoteEvaluationFailure):
        provider(request)
    assert persisted == [failure]


def test_controller_provider_rejects_artifact_mismatch_before_ssh(tmp_path):
    archive = tmp_path / "source.tar"
    archive.write_bytes(b"wrong")
    called = []
    with pytest.raises(EvaluationError, match="digest mismatch"):
        SshReviewedEvaluationProvider(lambda _: archive, known_hosts=tmp_path / "missing", invoke=lambda *a, **k: called.append(a))(parameters())
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
    remote_paths = {"/run/current-system/sw/bin/python3", *EXECUTABLES.values()}
    monkeypatch.setattr("tgw.nixos_reviewed_evaluation._digest_file", lambda path: "sha256:" + DIGEST if str(path).startswith(closure) or str(path) in remote_paths else original_digest(path))
    calls = []

    def fake_run(argv, *, cwd, timeout):
        calls.append(argv)
        if argv[-1] == "write-tree":
            return TREE
        if "drvPath" in argv[-1]:
            return "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-review.drv"
        if "build" in argv:
            return closure
        if "--requisites" in argv:
            return closure + "\n/nix/store/11111111111111111111111111111111-dependency\n"
        if "hash" in argv:
            return DIGEST
        if argv == [EXECUTABLES["systemd_analyze"], "--version"]:
            return "systemd 257\n"
        if argv == [EXECUTABLES["nix"], "--version"]:
            return "nix (Nix) 2.28.5\n"
        return ""

    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    result = execute_packet(packet(request, archive), run=fake_run, scratch_root=scratch, scratch_uid=os.geteuid())

    assert result["cleanup"] == "removed" and not list(scratch.iterdir())
    assert result["scratch_root"] == {"path": str(scratch), "created_by_attempt": False, "final_state": "retained-existing"}
    nix_calls = [call for call in calls if call[0] == EXECUTABLES["nix"] and "--offline" in call]
    assert nix_calls and all(call[1:5] == ["--offline", "--option", "substituters", ""] for call in nix_calls)
    assert all(["--option", "allow-import-from-derivation", "false"] == call[5:8] and "--no-write-lock-file" in call for call in nix_calls)
    assert not any(word in {"switch", "boot", "test", "profile"} for call in calls for word in call)
    assert set(result["unit_sha256"]) == set(request["unit_set"].split(","))


@pytest.mark.parametrize("change", [{"activate": "true"}, {"module_path": "../../etc/passwd"}, {"command": "id"}])
def test_remote_helper_rejects_broadened_request_before_scratch(tmp_path, change):
    archive = tmp_path / "source.tar"
    make_archive(archive)
    request = {**parameters(sha256(archive.read_bytes()).hexdigest()), **change}
    scratch = tmp_path / "scratch"
    with pytest.raises(ValueError):
        execute_packet(packet(request, archive), scratch_root=scratch, scratch_uid=os.geteuid())
    assert not scratch.exists()


def test_remote_helper_cleans_scratch_on_failure(tmp_path, monkeypatch):
    archive = tmp_path / "source.tar"
    make_archive(archive, commit="d" * 40)
    request = parameters(sha256(archive.read_bytes()).hexdigest())
    import tgw.nixos_reviewed_evaluation as provider_module

    request["provider_sha256"] = "sha256:" + sha256(Path(provider_module.__file__).read_bytes()).hexdigest()
    original_digest = provider_module._digest_file
    remote_paths = {provider_module.REMOTE_PYTHON, *EXECUTABLES.values()}
    monkeypatch.setattr(provider_module, "_digest_file", lambda path: "sha256:" + DIGEST if str(path) in remote_paths else original_digest(path))
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    with pytest.raises(EvaluationError, match="commit identity"):
        execute_packet(packet(request, archive), scratch_root=scratch, scratch_uid=os.geteuid())
    assert not list(scratch.iterdir())


def test_provider_source_has_no_shell_or_activation_escape_hatch():
    import tgw.nixos_reviewed_evaluation as provider_module

    source = Path(provider_module.__file__).read_text()
    assert "shell=True" not in source
    assert "nixos-rebuild" not in source
    assert "nix profile" not in source
    assert "/home/db/tgw-flake" not in source
    assert "from tgw" not in source and "import tgw" not in source
    assert 'SSH_EXECUTABLE = "/usr/bin/ssh"' in source
    assert '"-F"' in source and '"/dev/null"' in source


def test_remote_helper_rejects_git_control_files_and_scratch_symlink(tmp_path, monkeypatch):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": COMMIT}) as value:
        info = tarfile.TarInfo("trader-grims-warehouse/.git/config")
        info.size = 6
        value.addfile(info, io.BytesIO(b"[core]"))
    request = parameters(sha256(archive.read_bytes()).hexdigest())
    import tgw.nixos_reviewed_evaluation as provider_module

    request["provider_sha256"] = "sha256:" + sha256(Path(provider_module.__file__).read_bytes()).hexdigest()
    original_digest = provider_module._digest_file
    remote_paths = {provider_module.REMOTE_PYTHON, *EXECUTABLES.values()}
    monkeypatch.setattr(provider_module, "_digest_file", lambda path: "sha256:" + DIGEST if str(path) in remote_paths else original_digest(path))
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    with pytest.raises(EvaluationError, match="unsafe member"):
        execute_packet(packet(request, archive), scratch_root=scratch, scratch_uid=os.geteuid())
    target = tmp_path / "target"
    target.mkdir()
    scratch.rmdir()
    scratch.symlink_to(target, target_is_directory=True)
    with pytest.raises(EvaluationError, match="root-owned"):
        execute_packet(packet(request, archive), scratch_root=scratch, scratch_uid=os.geteuid())


def test_controller_rejects_archive_size_before_transport(tmp_path):
    archive = tmp_path / "large.tar"
    archive.write_bytes(b"x" * 2048)
    digest = sha256(archive.read_bytes()).hexdigest()
    request = parameters(digest)
    request["max_archive_bytes"] = "1024"
    with pytest.raises(EvaluationError, match="exceeds"):
        SshReviewedEvaluationProvider(lambda _: archive, known_hosts=tmp_path / "unused")(request)


def test_remote_helper_creates_and_rolls_back_exact_scratch_root(tmp_path, monkeypatch):
    archive = tmp_path / "source.tar"
    make_archive(archive, commit="d" * 40)
    request = parameters(sha256(archive.read_bytes()).hexdigest())
    import tgw.nixos_reviewed_evaluation as provider_module

    request["provider_sha256"] = "sha256:" + sha256(Path(provider_module.__file__).read_bytes()).hexdigest()
    original_digest = provider_module._digest_file
    remote_paths = {provider_module.REMOTE_PYTHON, *EXECUTABLES.values()}
    monkeypatch.setattr(provider_module, "_digest_file", lambda path: "sha256:" + DIGEST if str(path) in remote_paths else original_digest(path))
    scratch = tmp_path / "tgw-reviewed-evaluation"
    with pytest.raises(EvaluationError, match="commit identity"):
        execute_packet(packet(request, archive), scratch_root=scratch, scratch_uid=os.geteuid())
    assert not scratch.exists()


def test_scratch_root_rejects_symlink_or_wrong_mode_without_chmod(tmp_path):
    from tgw.nixos_reviewed_evaluation import _prepare_scratch_root

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    scratch = tmp_path / "tgw-reviewed-evaluation"
    scratch.symlink_to(target, target_is_directory=True)
    with pytest.raises(EvaluationError, match="root-owned"):
        _prepare_scratch_root(scratch, expected_uid=os.geteuid())
    scratch.unlink()
    scratch.mkdir(mode=0o755)
    with pytest.raises(EvaluationError, match="root-owned"):
        _prepare_scratch_root(scratch, expected_uid=os.geteuid())
    assert scratch.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize(
    "line",
    [
        "tgw-prod ssh-ed25519 AAAA",
        "100.107.99.66 ssh-dss AAAA",
        "100.107.99.66 ssh-ed25519 !!!",
        "100.107.99.66 ssh-ed25519 AAAA\n100.107.99.66 ssh-ed25519 BBBB",
    ],
)
def test_controller_rejects_nonexact_known_host_grammar(tmp_path, line):
    archive = tmp_path / "source.tar"
    make_archive(archive)
    digest = sha256(archive.read_bytes()).hexdigest()
    request = parameters(digest)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(line)
    known_hosts.chmod(0o600)
    request["ssh_sha256"] = "sha256:" + sha256(Path(SSH_EXECUTABLE).read_bytes()).hexdigest()
    request["known_hosts_sha256"] = "sha256:" + sha256(known_hosts.read_bytes()).hexdigest()
    with pytest.raises(EvaluationError, match="one admitted host key"):
        SshReviewedEvaluationProvider(lambda _: archive, known_hosts=known_hosts, invoke=lambda *a, **k: None)(request)


@pytest.mark.parametrize("kind", ["duplicate", "special", "wrong_root"])
def test_remote_archive_rejects_ambiguous_member_sets(tmp_path, monkeypatch, kind):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": COMMIT}) as value:
        name = "other/file" if kind == "wrong_root" else "trader-grims-warehouse/file"
        info = tarfile.TarInfo(name)
        if kind == "special":
            info.type = tarfile.FIFOTYPE
        else:
            info.size = 1
        value.addfile(info, None if kind == "special" else io.BytesIO(b"x"))
        if kind == "duplicate":
            value.addfile(info, io.BytesIO(b"x"))
    request = parameters(sha256(archive.read_bytes()).hexdigest())
    import tgw.nixos_reviewed_evaluation as provider_module

    request["provider_sha256"] = "sha256:" + sha256(Path(provider_module.__file__).read_bytes()).hexdigest()
    original_digest = provider_module._digest_file
    remote_paths = {provider_module.REMOTE_PYTHON, *EXECUTABLES.values()}
    monkeypatch.setattr(provider_module, "_digest_file", lambda path: "sha256:" + DIGEST if str(path) in remote_paths else original_digest(path))
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    with pytest.raises(EvaluationError, match="unsafe|single root|duplicate"):
        execute_packet(packet(request, archive), scratch_root=scratch, scratch_uid=os.geteuid())
    assert not list(scratch.iterdir())


def test_real_openssh_reads_sealed_parent_memfd_and_rejects_wrong_key(tmp_path):
    sshd = Path("/usr/sbin/sshd")
    if not sshd.is_file():
        pytest.skip("local OpenSSH server is unavailable")
    host_key = tmp_path / "host_key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(host_key)], check=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "sshd_config"
    config.write_text(
        f"Port {port}\nListenAddress 127.0.0.1\nHostKey {host_key}\nPidFile {tmp_path / 'sshd.pid'}\nUsePAM no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n"
    )
    server = subprocess.Popen([str(sshd), "-D", "-e", "-f", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 3
        while time.time() < deadline:
            with socket.socket() as client:
                if client.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        else:
            pytest.skip("local sshd could not start unprivileged")
        public = host_key.with_suffix(".pub").read_text().split()
        correct = f"[127.0.0.1]:{port} {public[0]} {public[1]}\n".encode()

        def connect(content):
            fd = _sealed_memfd("known-hosts-test", content)
            try:
                return subprocess.run(
                    [
                        SSH_EXECUTABLE,
                        "-F",
                        "/dev/null",
                        "-oBatchMode=yes",
                        "-oClearAllForwardings=yes",
                        "-oStrictHostKeyChecking=yes",
                        f"-oUserKnownHostsFile=/proc/{os.getpid()}/fd/{fd}",
                        "-p",
                        str(port),
                        "127.0.0.1",
                        "true",
                    ],
                    pass_fds=(fd,),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            finally:
                os.close(fd)

        accepted_key = connect(correct)
        assert "REMOTE HOST IDENTIFICATION HAS CHANGED" not in accepted_key.stderr and "Host key verification failed" not in accepted_key.stderr
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "wrong")], check=True)
        wrong_public = (tmp_path / "wrong.pub").read_text().split()
        rejected_key = connect(f"[127.0.0.1]:{port} {wrong_public[0]} {wrong_public[1]}\n".encode())
        assert "REMOTE HOST IDENTIFICATION HAS CHANGED" in rejected_key.stderr or "Host key verification failed" in rejected_key.stderr
    finally:
        server.terminate()
        server.wait(timeout=5)
