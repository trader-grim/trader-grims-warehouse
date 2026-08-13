import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tgw.a3_observation_authority import (
    DurableObservationToken,
    ObservationAlreadyConsumed,
    ObservationDispatchAmbiguous,
    ReadOnlyObservationController,
    ReadOnlyObservationGrant,
)
from tgw.a3_preintegration_observation import ImmutableEvidenceStore, ObservationHold, digest, make_request, observe_repository


def _request():
    return make_request(
        operation_id="authority",
        transport={name: "sha256:" + "1" * 64 for name in ("ssh_sha256", "known_hosts_sha256", "identity_sha256", "helper_sha256", "python_sha256", "git_sha256")},
    )


def _identity(path: Path):
    st = path.stat()
    return {"path": str(path), "uid": st.st_uid, "gid": st.st_gid, "mode": st.st_mode & 0o7777, "dev": st.st_dev, "ino": st.st_ino, "nlink": st.st_nlink}


def _grant(request, token_root: Path, evidence_root: Path):
    return ReadOnlyObservationGrant.issue(
        request=request,
        composition_sha256="sha256:" + "2" * 64,
        token_root_identity=_identity(token_root),
        evidence_root_identity=_identity(evidence_root),
        host_state_dependency=request["host_state_dependency"],
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )


class Provider:
    def __init__(self, *, ready=True, fail=False, result=None):
        self.is_ready, self.fail, self.calls, self.result = ready, fail, 0, result

    def ready(self, request):
        return self.is_ready

    def prepare_launch(self, request):
        def launch():
            self.calls += 1
            if self.fail:
                raise RuntimeError("after dispatch")
            return self.result

        return launch


def test_hold_before_ready_does_not_consume(tmp_path: Path) -> None:
    request = _request()
    provider = Provider(ready=False)
    token_root = tmp_path / "tokens"
    token_root.mkdir(mode=0o700)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    grant = _grant(request, token_root, evidence_root)
    controller = ReadOnlyObservationController(grant=grant, provider=provider, composition_sha256="sha256:" + "2" * 64)
    with pytest.raises(ObservationHold):
        controller.execute(request)
    assert controller.consumed is False and provider.calls == 0


def test_first_dispatch_consumes_exactly_once(tmp_path: Path) -> None:
    request = _request()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True)
    (repo / "flake.lock").write_text('{"version":7,"root":"root","nodes":{"root":{}}}\n')
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=repo, check=True)
    receipt, archive = observe_repository(repo, request)
    provider = Provider(result={"receipt": receipt, "archive": archive})
    token_root = tmp_path / "tokens"
    token_root.mkdir(mode=0o700)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    grant = _grant(request, token_root, evidence_root)
    controller = ReadOnlyObservationController(
        grant=grant,
        provider=provider,
        composition_sha256="sha256:" + "2" * 64,
        evidence_store=ImmutableEvidenceStore(evidence_root),
        token=DurableObservationToken(str(token_root), grant.value["grant_sha256"]),
    )
    result = controller.execute(request)
    assert result["terminal"]["outcome"] == "PASS"
    assert result["archive_sha256"] == digest(archive)
    with pytest.raises(ObservationAlreadyConsumed):
        controller.execute(request)
    assert provider.calls == 1


def test_postdispatch_failure_is_ambiguous_and_consumed(tmp_path: Path) -> None:
    request = _request()
    provider = Provider(fail=True)
    token_root = tmp_path / "tokens"
    token_root.mkdir(mode=0o700)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    grant = _grant(request, token_root, evidence_root)
    controller = ReadOnlyObservationController(
        grant=grant,
        provider=provider,
        composition_sha256="sha256:" + "2" * 64,
        evidence_store=ImmutableEvidenceStore(evidence_root),
        token=DurableObservationToken(str(token_root), grant.value["grant_sha256"]),
    )
    with pytest.raises(ObservationDispatchAmbiguous):
        controller.execute(request)
    assert controller.consumed is True and provider.calls == 1


def test_grant_rejects_bool_attempts(tmp_path: Path) -> None:
    token_root = tmp_path / "tokens"
    token_root.mkdir(mode=0o700)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    grant = dict(_grant(_request(), token_root, evidence_root).value)
    grant["attempts"] = True
    with pytest.raises(Exception):
        ReadOnlyObservationGrant.validate(grant)


def test_provider_cannot_return_without_controller_dispatch(tmp_path: Path) -> None:
    class SkippingProvider(Provider):
        def prepare_launch(self, request):
            return {"receipt": {}, "archive": b"fabricated"}

    request = _request()
    token_root = tmp_path / "tokens"
    token_root.mkdir(mode=0o700)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    grant = _grant(request, token_root, evidence_root)
    controller = ReadOnlyObservationController(
        grant=grant,
        provider=SkippingProvider(),
        composition_sha256="sha256:" + "2" * 64,
        evidence_store=ImmutableEvidenceStore(evidence_root),
        token=DurableObservationToken(str(token_root), grant.value["grant_sha256"]),
    )
    with pytest.raises(ObservationDispatchAmbiguous):
        controller.execute(request)
    assert controller.consumed is False
