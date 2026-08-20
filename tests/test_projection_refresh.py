from copy import deepcopy

import pytest

from tgw.projection_refresh import ProjectionRefreshError, compile_projection_refresh

HASH = "sha256:" + "a" * 64


def revision(source="source", health="READY"):
    return {"source": source, "materialization": HASH, "build": HASH, "built_at": "2026-08-20T12:00:00+00:00", "health": health}


def request():
    values = {name: revision(name) for name in ("plan", "capability_graph", "code_graph", "workflow", "actor_contract")}
    return {
        "schema": "tgw-w18-projection-refresh-request/v1",
        "lease": {"id": "fleet", "generation": 1},
        "desired": deepcopy(values),
        "observed": deepcopy(values),
        "actors": [{"id": "codex", "generation": HASH, "status": "READY"}],
        "refresh": {"predecessor": HASH, "successor": HASH, "outcome": "HEALTHY"},
    }


def test_fresh_refresh_is_deterministic_and_non_activating():
    assert compile_projection_refresh(request=request()) == compile_projection_refresh(request=request())
    assert compile_projection_refresh(request=request())["status"] == "FRESH"


@pytest.mark.parametrize("projection", ["plan", "capability_graph", "code_graph", "workflow", "actor_contract"])
def test_stale_projection_holds_all_launch_consumers(projection):
    value = request()
    value["observed"][projection]["health"] = "STALE"
    receipt = compile_projection_refresh(request=value)
    assert receipt["status"] == "HOLD"
    assert f"stale-projection:{projection}" in receipt["reasons"]


def test_failed_refresh_requires_rollback_and_mixed_actor_quarantines():
    failed = request()
    failed["refresh"]["outcome"] = "FAILED"
    assert compile_projection_refresh(request=failed)["status"] == "ROLLBACK_REQUIRED"
    mixed = request()
    mixed["actors"][0]["status"] = "DIVERGENT"
    assert compile_projection_refresh(request=mixed)["status"] == "QUARANTINED"


def test_malformed_lease_or_actor_generation_fails_closed():
    bad = request()
    bad["lease"]["generation"] = True
    with pytest.raises(ProjectionRefreshError):
        compile_projection_refresh(request=bad)
    bad = request()
    bad["actors"][0]["generation"] = "bad"
    with pytest.raises(ProjectionRefreshError):
        compile_projection_refresh(request=bad)
