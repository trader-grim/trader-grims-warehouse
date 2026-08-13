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


def _grant(request):
    return ReadOnlyObservationGrant.issue(
        request=request,
        composition_sha256="sha256:" + "2" * 64,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )


class Provider:
    def __init__(self, *, ready=True, fail=False, result=None):
        self.is_ready, self.fail, self.calls, self.result = ready, fail, 0, result

    def ready(self, request):
        return self.is_ready

    def observe(self, request, *, on_dispatch):
        on_dispatch()
        self.calls += 1
        if self.fail:
            raise RuntimeError("after dispatch")
        return self.result


def test_hold_before_ready_does_not_consume() -> None:
    request = _request()
    provider = Provider(ready=False)
    grant = _grant(request)
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
    grant = _grant(request)
    token_root = tmp_path / "tokens"
    token_root.mkdir(mode=0o700)
    controller = ReadOnlyObservationController(
        grant=grant,
        provider=provider,
        composition_sha256="sha256:" + "2" * 64,
        evidence_store=ImmutableEvidenceStore(tmp_path / "evidence"),
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
    grant = _grant(request)
    token_root = tmp_path / "tokens"
    token_root.mkdir(mode=0o700)
    controller = ReadOnlyObservationController(
        grant=grant,
        provider=provider,
        composition_sha256="sha256:" + "2" * 64,
        evidence_store=ImmutableEvidenceStore(tmp_path / "evidence"),
        token=DurableObservationToken(str(token_root), grant.value["grant_sha256"]),
    )
    with pytest.raises(ObservationDispatchAmbiguous):
        controller.execute(request)
    assert controller.consumed is True and provider.calls == 1
