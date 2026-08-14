from __future__ import annotations

import hashlib
import subprocess
import time
from copy import deepcopy
from pathlib import Path

import pytest

from tgw.a3_preintegration_observation import (
    Composition,
    EvidencePersistenceAmbiguous,
    ImmutableEvidenceStore,
    ObservationError,
    ObservationHold,
    SshObservationProvider,
    _fixture_source_descriptor,
    canonical,
    decode_helper_response,
    digest,
    encode_helper_response,
    make_request,
    observe_repository,
    persist_evidence,
    replay_archive,
    terminal,
    validate_receipt,
    validate_request,
    validate_source_descriptor,
    validate_terminal,
)


def _transport() -> dict[str, str]:
    return {name: "sha256:" + hashlib.sha256(name.encode()).hexdigest() for name in ("ssh_sha256", "known_hosts_sha256", "identity_sha256", "helper_sha256", "python_sha256", "git_sha256")}


def _repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "branch", "-m", "master"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=path, check=True)
    (path / "flake.lock").write_text('{"version":7,"root":"root","nodes":{"root":{}}}\n')
    (path / "flake.nix").write_text("{ outputs = _: {}; }\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True)
    return path


def test_clean_observation_is_zero_effect_and_archive_bound(tmp_path: Path) -> None:
    request = make_request(operation_id="observe-1", transport=_transport())
    receipt, archive = observe_repository(_repo(tmp_path), request)
    assert receipt["outcome"] == "PASS"
    assert receipt["repository"]["archive_sha256"] == digest(archive)
    assert not any(receipt["effects"].values())
    paths = ImmutableEvidenceStore(tmp_path / "evidence").persist(receipt, archive)
    assert all(path.stat().st_mode & 0o222 == 0 for path in paths)


def test_dirty_repository_holds_without_archive(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "dirty").write_text("x")
    with pytest.raises(ObservationHold):
        observe_repository(repo, make_request(operation_id="dirty", transport=_transport()))


@pytest.mark.parametrize("relative", ["objects/info/alternates", "info/grafts"])
def test_repository_rejects_alternates_and_grafts(tmp_path: Path, relative: str) -> None:
    repo = _repo(tmp_path)
    path = repo / ".git" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("/untrusted\n")
    with pytest.raises(ObservationHold):
        observe_repository(repo, make_request(operation_id="graph", transport=_transport()))


def test_repository_rejects_replace_refs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    replace = repo / ".git/refs/replace"
    replace.mkdir(parents=True)
    (replace / ("1" * 40)).write_text("2" * 40)
    with pytest.raises(ObservationHold):
        observe_repository(repo, make_request(operation_id="replace", transport=_transport()))


def test_repository_rejects_gitfile_worktree_indirection(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    linked = tmp_path / "linked"
    subprocess.run(["git", "worktree", "add", "-q", str(linked)], cwd=repo, check=True)
    with pytest.raises(ObservationError, match=".git is not a directory"):
        observe_repository(linked, make_request(operation_id="gitfile", transport=_transport()))


@pytest.mark.parametrize(("field", "value"), [("identity_sha256", "ambient"), ("helper_sha256", "sha256:0")])
def test_transport_identity_is_closed(field: str, value: str) -> None:
    request = make_request(operation_id="closed", transport=_transport())
    request["transport"][field] = value
    with pytest.raises(ObservationError):
        validate_request(request)


def test_request_binds_fresh_host_repository_master_authority() -> None:
    request = make_request(operation_id="master-authority", transport=_transport())
    assert request["target"]["branch"] == "master"
    assert request["host_state_dependency"]["repository"]["branch"] == "master"

    changed = deepcopy(request)
    changed["target"]["branch"] = "main"
    changed["request_sha256"] = digest(canonical({key: value for key, value in changed.items() if key != "request_sha256"}))
    with pytest.raises(ObservationError, match="target is not exact"):
        validate_request(changed)

    changed = deepcopy(request)
    changed["host_state_dependency"]["repository"]["branch"] = "main"
    changed["request_sha256"] = digest(canonical({key: value for key, value in changed.items() if key != "request_sha256"}))
    with pytest.raises(ObservationError, match="host repository authority is invalid"):
        validate_request(changed)


def test_archive_xy_and_receipt_mutations_rejected(tmp_path: Path) -> None:
    request = make_request(operation_id="xy", transport=_transport())
    receipt, archive = observe_repository(_repo(tmp_path), request)
    assert digest(archive + b"x") != receipt["repository"]["archive_sha256"]
    changed = deepcopy(receipt)
    changed["effects"]["nix"] = True
    with pytest.raises(ObservationError):
        validate_receipt(changed, request)


def test_default_composition_is_fail_closed() -> None:
    with pytest.raises(ObservationHold):
        Composition().execute(make_request(operation_id="held", transport=_transport()))


def test_immutable_store_rejects_replay(tmp_path: Path) -> None:
    request = make_request(operation_id="once", transport=_transport())
    receipt, archive = observe_repository(_repo(tmp_path), request)
    store = ImmutableEvidenceStore(tmp_path / "evidence")
    store.persist(receipt, archive)
    with pytest.raises(FileExistsError):
        store.persist(receipt, archive)


def test_helper_frame_binds_exact_archive_bytes(tmp_path: Path) -> None:
    request = make_request(operation_id="frame", transport=_transport())
    receipt, archive = observe_repository(_repo(tmp_path), request)
    assert decode_helper_response(encode_helper_response(receipt, archive), request) == (receipt, archive)
    changed = bytearray(encode_helper_response(receipt, archive))
    changed[-1] ^= 1
    with pytest.raises(ObservationError):
        decode_helper_response(bytes(changed), request)


@pytest.mark.parametrize("raw", [b"", b"x" * 15, b"\0" * 16, (2**63).to_bytes(8, "big") + b"\0" * 8])
def test_helper_frame_rejects_truncation_and_bounds(raw: bytes) -> None:
    with pytest.raises(ObservationError):
        decode_helper_response(raw, make_request(operation_id="bad-frame", transport=_transport()))


def test_mounted_source_descriptor_rejects_independent_identity_mutation() -> None:
    source = _fixture_source_descriptor()
    source["helper_sha256"] = "sha256:" + "9" * 64
    with pytest.raises(ObservationError):
        validate_source_descriptor(source)


@pytest.mark.parametrize(
    ("outcome", "stage", "code", "dispatched"),
    [
        ("PASS", "complete", "NONE", True),
        ("HOLD", "predispatch", "PROVIDER_NOT_READY", False),
        ("FAILED", "helper", "HELPER_INVALID", True),
        ("AMBIGUOUS", "dispatch", "POSTDISPATCH_UNCERTAIN", True),
        ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", True),
    ],
)
def test_closed_terminal_tuples(outcome: str, stage: str, code: str, dispatched: bool) -> None:
    value = terminal(
        outcome=outcome,
        stage=stage,
        code=code,
        dispatched=dispatched,
        request_sha256="sha256:" + "1" * 64,
        observed_at="2026-08-13T00:00:00+00:00",
    )
    assert validate_terminal(value) == value
    changed = deepcopy(value)
    changed["dispatched"] = not dispatched
    with pytest.raises(ObservationError):
        validate_terminal(changed)


def test_persistence_failure_retains_typed_ambiguity(tmp_path: Path) -> None:
    request = make_request(operation_id="persist", transport=_transport())
    receipt, archive = observe_repository(_repo(tmp_path), request)

    class Broken:
        def persist(self, *args, **kwargs):
            raise PermissionError("denied")

    with pytest.raises(EvidencePersistenceAmbiguous) as caught:
        persist_evidence(Broken(), request=request, receipt=receipt, archive=archive, observed_at="2026-08-13T00:00:00+00:00")
    assert caught.value.terminal["outcome"] == "AMBIGUOUS"
    assert caught.value.terminal["stage"] == "persistence"


def test_sealed_transport_uses_exact_identity_and_frame(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    fake = tmp_path / "ssh"
    response_path = tmp_path / "response"
    fake.write_text("#!/usr/bin/python3\nimport os,pathlib\nos.read(0,1<<20)\nos.write(1,pathlib.Path(" + repr(str(response_path)) + ").read_bytes())\n")
    fake.chmod(0o755)
    hosts = tmp_path / "hosts"
    hosts.write_text("tgw-prod ssh-ed25519 a2V5\n")
    hosts.chmod(0o444)
    identity = tmp_path / "identity"
    identity.write_text("identity\n")
    identity.chmod(0o400)
    keygen = tmp_path / "ssh-keygen"
    keygen.write_text("#!/bin/sh\necho 'ssh-ed25519 a2V5'\n")
    keygen.chmod(0o755)
    helper = tmp_path / "helper"
    helper.write_bytes(Path("src/tgw/a3_preintegration_observation.py").read_bytes())
    helper.chmod(0o444)
    source = _fixture_source_descriptor()
    source["helper_sha256"] = digest(helper.read_bytes())
    source["descriptor_sha256"] = digest(canonical({k: v for k, v in source.items() if k != "descriptor_sha256"}))
    transport = {
        "ssh_sha256": digest(fake.read_bytes()),
        "ssh_keygen_sha256": digest(keygen.read_bytes()),
        "known_hosts_sha256": digest(hosts.read_bytes()),
        "identity_sha256": digest(identity.read_bytes()),
        "identity_public": "ssh-ed25519 a2V5",
        "helper_sha256": digest(helper.read_bytes()),
        "python_sha256": "sha256:" + "5" * 64,
        "git_sha256": "sha256:" + "6" * 64,
    }
    request = make_request(operation_id="ssh-frame", transport=transport, source=source)
    receipt, archive = observe_repository(repo, request)
    response_path.write_bytes(encode_helper_response(receipt, archive))
    provider = SshObservationProvider(request, fake, hosts, identity, helper, "/usr/bin/python3", request["source"], keygen)
    assert provider.ready(request)
    result = provider.observe(request, on_dispatch=lambda: None)
    assert result["receipt"]["repository"]["archive_sha256"] == digest(result["archive"])


def test_sealed_transport_rejects_ambient_identity_mutation(tmp_path: Path) -> None:
    request = make_request(operation_id="identity", transport=_transport())
    missing = tmp_path / "missing"
    provider = SshObservationProvider(request, missing, missing, missing, missing, "/usr/bin/python3")
    assert provider.ready(request) is False


def test_production_provider_rejects_self_consistent_descriptor_xy(tmp_path: Path) -> None:
    request = make_request(operation_id="descriptor-xy", transport=_transport())
    changed = deepcopy(request["source"])
    changed["catalog_sha256"] = "sha256:" + "8" * 64
    changed["descriptor_sha256"] = digest(canonical({k: v for k, v in changed.items() if k != "descriptor_sha256"}))
    provider = SshObservationProvider(request, tmp_path / "ssh", tmp_path / "hosts", tmp_path / "key", tmp_path / "helper", "/usr/bin/python3", changed)
    assert provider.ready(request) is False


def test_provider_rejects_private_public_key_mismatch(tmp_path: Path) -> None:
    request = make_request(operation_id="key-mismatch", transport=_transport())
    request["transport"]["identity_public"] = "ssh-ed25519 d3Jvbmc="
    request["request_sha256"] = digest(canonical({k: v for k, v in request.items() if k != "request_sha256"}))
    missing = tmp_path / "missing"
    provider = SshObservationProvider(request, missing, missing, missing, missing, "/usr/bin/python3", request["source"], missing)
    assert provider.ready(request) is False


@pytest.mark.parametrize("relative", ["objects/info/http-alternates", "commondir", "shallow"])
def test_repository_rejects_common_and_shallow_state(tmp_path: Path, relative: str) -> None:
    repo = _repo(tmp_path)
    path = repo / ".git" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("external\n")
    with pytest.raises(ObservationHold):
        observe_repository(repo, make_request(operation_id="common-state", transport=_transport()))


@pytest.mark.parametrize("relative,content", [("config.worktree", "[core]\n\tbare = false\n"), ("config", "[extensions]\n\tworktreeConfig = true\n")])
def test_repository_rejects_worktree_specific_config(tmp_path: Path, relative: str, content: str) -> None:
    repo = _repo(tmp_path)
    (repo / ".git" / relative).write_text(content)
    with pytest.raises(ObservationHold):
        observe_repository(repo, make_request(operation_id="worktree-config", transport=_transport()))


def test_archive_replay_rejects_missing_lock_root_node(tmp_path: Path) -> None:
    request = make_request(operation_id="missing-lock-root", transport=_transport())
    repo = _repo(tmp_path)
    (repo / "flake.lock").write_text('{"version":7,"root":"missing","nodes":{"root":{}}}\n')
    subprocess.run(["git", "add", "-f", "flake.lock"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "bad lock"], cwd=repo, check=True)
    receipt, archive = observe_repository(repo, request)
    with pytest.raises(ObservationError, match="input graph"):
        replay_archive(archive, receipt, request)


def test_transport_kills_term_ignoring_descendant_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    fake = tmp_path / "ssh"
    fake.write_text(
        "#!/usr/bin/python3\nimport os,signal,subprocess,time\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        f"p=subprocess.Popen(['/usr/bin/python3','-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)']);open({str(pid_file)!r},'w').write(str(p.pid));time.sleep(60)\n"
    )
    fake.chmod(0o755)
    hosts = tmp_path / "hosts"
    hosts.write_text("tgw-prod ssh-ed25519 a2V5\n")
    hosts.chmod(0o444)
    identity = tmp_path / "identity"
    identity.write_text("identity\n")
    identity.chmod(0o400)
    helper = tmp_path / "helper"
    helper.write_bytes(Path("src/tgw/a3_preintegration_observation.py").read_bytes())
    helper.chmod(0o444)
    source = _fixture_source_descriptor()
    source["helper_sha256"] = digest(helper.read_bytes())
    source["descriptor_sha256"] = digest(canonical({k: v for k, v in source.items() if k != "descriptor_sha256"}))
    transport = {
        "ssh_sha256": digest(fake.read_bytes()),
        "known_hosts_sha256": digest(hosts.read_bytes()),
        "identity_sha256": digest(identity.read_bytes()),
        "helper_sha256": digest(helper.read_bytes()),
        "python_sha256": "sha256:" + "5" * 64,
        "git_sha256": "sha256:" + "6" * 64,
    }
    request = make_request(operation_id="killpg", transport=transport, source=source)
    request["bounds"]["timeout_seconds"] = 1
    request["request_sha256"] = digest(canonical({k: v for k, v in request.items() if k != "request_sha256"}))
    provider = SshObservationProvider(request, fake, hosts, identity, helper, "/usr/bin/python3", request["source"])
    with pytest.raises(ObservationError, match="timed out"):
        provider.observe(request, on_dispatch=lambda: None)
    for _ in range(20):
        if pid_file.exists() and not Path("/proc", pid_file.read_text()).exists():
            break
        time.sleep(0.05)
    assert pid_file.exists() and not Path("/proc", pid_file.read_text()).exists()
