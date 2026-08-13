from __future__ import annotations

import hashlib
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from tgw.a3_preintegration_observation import (
    Composition,
    EvidencePersistenceAmbiguous,
    ImmutableEvidenceStore,
    ObservationError,
    ObservationHold,
    decode_helper_response,
    digest,
    encode_helper_response,
    fixture_source_descriptor,
    make_request,
    observe_repository,
    persist_evidence,
    terminal,
    validate_receipt,
    validate_request,
    validate_source_descriptor,
    validate_terminal,
)


def _transport() -> dict[str, str]:
    return {name: "sha256:" + hashlib.sha256(name.encode()).hexdigest() for name in ("ssh_sha256", "known_hosts_sha256", "identity_sha256", "helper_sha256")}


def _repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
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


@pytest.mark.parametrize(("field", "value"), [("identity_sha256", "ambient"), ("helper_sha256", "sha256:0")])
def test_transport_identity_is_closed(field: str, value: str) -> None:
    request = make_request(operation_id="closed", transport=_transport())
    request["transport"][field] = value
    with pytest.raises(ObservationError):
        validate_request(request)


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
    source = fixture_source_descriptor()
    source["helper_sha256"] = "sha256:" + "9" * 64
    with pytest.raises(ObservationError):
        validate_source_descriptor(source)


@pytest.mark.parametrize(
    ("outcome", "stage", "code", "dispatched"),
    [
        ("PASS", "complete", "NONE", False),
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
