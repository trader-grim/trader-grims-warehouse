from datetime import datetime, timedelta, timezone

import pytest

from tgw.a3_observation_authority import (
    ObservationAlreadyConsumed,
    ObservationDispatchAmbiguous,
    ReadOnlyObservationController,
    ReadOnlyObservationGrant,
)
from tgw.a3_preintegration_observation import ObservationHold, make_request


def _request():
    return make_request(
        operation_id="authority",
        transport={name: "sha256:" + "1" * 64 for name in ("ssh_sha256", "known_hosts_sha256", "identity_sha256", "helper_sha256", "python_sha256", "git_sha256")},
    )


def _grant(request):
    return ReadOnlyObservationGrant.issue(
        request=request,
        solution_sha256="solution:test",
        closure_sha256="closure:test",
        composition_sha256="sha256:" + "2" * 64,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )


class Provider:
    def __init__(self, *, ready=True, fail=False):
        self.is_ready, self.fail, self.calls = ready, fail, 0

    def ready(self, request):
        return self.is_ready

    def observe(self, request):
        self.calls += 1
        if self.fail:
            raise RuntimeError("after dispatch")
        return {"evidence": ["observed"]}


def test_hold_before_ready_does_not_consume() -> None:
    request = _request()
    provider = Provider(ready=False)
    controller = ReadOnlyObservationController(grant=_grant(request), provider=provider, composition_sha256="sha256:" + "2" * 64)
    with pytest.raises(ObservationHold):
        controller.execute(request)
    assert controller.consumed is False and provider.calls == 0


def test_first_dispatch_consumes_exactly_once() -> None:
    request = _request()
    provider = Provider()
    controller = ReadOnlyObservationController(grant=_grant(request), provider=provider, composition_sha256="sha256:" + "2" * 64)
    assert controller.execute(request) == {"evidence": ["observed"]}
    with pytest.raises(ObservationAlreadyConsumed):
        controller.execute(request)
    assert provider.calls == 1


def test_postdispatch_failure_is_ambiguous_and_consumed() -> None:
    request = _request()
    provider = Provider(fail=True)
    controller = ReadOnlyObservationController(grant=_grant(request), provider=provider, composition_sha256="sha256:" + "2" * 64)
    with pytest.raises(ObservationDispatchAmbiguous):
        controller.execute(request)
    assert controller.consumed is True and provider.calls == 1
