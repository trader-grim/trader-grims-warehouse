"""Semi-automatic executor selection (tgw.model_selector)."""

from __future__ import annotations

import json

import pytest

from tgw import model_selector as ms


def _write(tmp_path, data):
    path = tmp_path / "model-availability.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _no_env_pin(monkeypatch):
    monkeypatch.delenv("TGW_IMPLEMENT_EXECUTOR", raising=False)
    monkeypatch.delenv("TGW_REVIEW_EXECUTOR", raising=False)
    monkeypatch.delenv("TGW_MODEL_AVAILABILITY", raising=False)


@pytest.fixture(autouse=True)
def _no_observation_holds(monkeypatch):
    # hermetic by default: no live dispatch-outcome holds unless a test
    # installs its own recent_status stub (never a real file).
    monkeypatch.setattr(ms.model_observations, "recent_status", lambda executor, **k: None)


def _stub_holds(monkeypatch, **holds):
    """Install a fake observations store: named executors are held, the rest
    are not. No real file is touched."""
    def fake(executor, **kwargs):
        if executor in holds:
            return ("held", holds[executor])
        return None
    monkeypatch.setattr(ms.model_observations, "recent_status", fake)


_BASE = {
    "updated": "2026-09-04",
    "executors": {
        "codex": {"available": False, "reason": "subscription lapsed"},
        "claude": {"available": True},
        "manual": {"available": True},
    },
    "roles": {
        "implementation": {"prefer": ["codex", "claude", "manual"]},
        "review": {"prefer": ["claude", "codex", "manual"]},
    },
}


def test_picks_first_available_in_policy_and_skips_the_unavailable():
    sel = ms.select_executor("implementation", availability=_BASE)
    assert sel.status == "SELECTED"
    assert sel.executor == "claude"  # codex is first but unavailable
    assert "policy" in sel.reason
    assert sel.considered == ("codex", "claude", "manual")
    assert sel.availability_updated == "2026-09-04"


def test_receipt_shape():
    r = ms.select_executor("review", availability=_BASE).receipt()
    assert r["schema"] == ms.SCHEMA
    assert r["role"] == "review"
    assert r["executor"] == "claude"
    assert set(r) >= {"observed_at", "status", "reason", "considered", "availability_updated"}


def test_abstains_when_nothing_in_policy_is_available_no_silent_fallback():
    data = json.loads(json.dumps(_BASE))
    data["executors"]["claude"]["available"] = False
    data["executors"]["manual"]["available"] = False
    sel = ms.select_executor("implementation", availability=data)
    assert sel.status == "ABSTAIN"
    assert sel.executor is None
    assert "subscription lapsed" in sel.reason  # carries the held reasons


def test_env_pin_wins_only_if_available(monkeypatch):
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "claude")
    sel = ms.select_executor("implementation", availability=_BASE)
    assert sel.executor == "claude" and "pinned" in sel.reason


def test_env_pin_wins_but_reason_flags_when_file_says_unavailable(monkeypatch):
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "codex")
    sel = ms.select_executor("implementation", availability=_BASE)
    assert sel.executor == "codex"
    assert "subscription lapsed" in sel.reason


def test_env_pin_to_a_name_that_is_not_a_known_executor_is_rejected(monkeypatch):
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "bogus")
    with pytest.raises(ms.ModelSelectorError, match="not a known executor"):
        ms.select_executor("implementation", availability=_BASE)


def test_unknown_executor_in_file_is_rejected(tmp_path, monkeypatch):
    path = _write(tmp_path, {**_BASE, "executors": {"gpt5": {"available": True}}})
    monkeypatch.setenv("TGW_MODEL_AVAILABILITY", str(path))
    with pytest.raises(ms.ModelSelectorError, match="unknown executor"):
        ms.load_availability()


def test_malformed_file_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "model-availability.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("TGW_MODEL_AVAILABILITY", str(path))
    with pytest.raises(ms.ModelSelectorError):
        ms.load_availability()


def test_missing_file_falls_back_to_manual_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setenv("TGW_MODEL_AVAILABILITY", str(tmp_path / "nope.json"))
    sel = ms.select_executor("implementation")
    assert sel.executor == "manual"


def test_committed_default_file_is_valid_and_selects_something():
    data = ms.load_availability(ms._REPO_DEFAULT)
    for role in ("implementation", "review"):
        sel = ms.select_executor(role, availability=data)
        assert sel.status == "SELECTED"
        assert sel.executor in ms.known_executors()


def test_known_executors_come_from_the_coding_executor_catalogue(tmp_path, monkeypatch):
    # no hard-coded set: point the catalogue at a file with one made-up
    # executor and the selector's known set follows (plus the 'manual' builtin).
    import json

    cat = tmp_path / "executors.json"
    cat.write_text(json.dumps({"executors": {
        "acme": {"binary_name": "acme", "install_source": None, "install_target_path": None,
                 "runtime_deps": [], "verify_cmd": None, "credential_env": ["ACME_API_KEY"],
                 "auth_file": None, "enabled": True},
    }}))
    monkeypatch.setenv("TGW_CODING_EXECUTORS", str(cat))
    assert ms.known_executors() == frozenset({"acme", "manual"})
    # an availability file naming a catalogue executor now parses
    path = _write(tmp_path, {**_BASE, "executors": {"acme": {"available": True}},
                             "roles": {"implementation": {"prefer": ["acme"]},
                                       "review": {"prefer": ["acme"]}}})
    monkeypatch.setenv("TGW_MODEL_AVAILABILITY", str(path))
    assert ms.select_executor("implementation").executor == "acme"


# --------------------------------------------------------------------------- #
# dispatch-outcome holds (LEAF-11-8 / Todo 1956 slice)
# --------------------------------------------------------------------------- #

_HOLD_BASE = {
    "updated": "2026-09-04",
    "executors": {
        "opencode": {"available": True},
        "claude": {"available": True},
        "manual": {"available": True},
    },
    "roles": {
        "implementation": {"prefer": ["opencode", "claude", "manual"]},
        "review": {"prefer": ["claude", "opencode", "manual"]},
    },
}


def test_select_skips_a_held_executor_and_picks_the_next_with_a_reason(monkeypatch):
    _stub_holds(monkeypatch, opencode="unavailable 300s ago (cooldown 60m): quota wall")
    sel = ms.select_executor("implementation", availability=_HOLD_BASE)
    assert sel.status == "SELECTED"
    assert sel.executor == "claude"  # opencode is first but held
    assert "hold" in sel.reason.lower()
    assert "opencode" in sel.reason
    assert sel.considered == ("opencode", "claude", "manual")


def test_select_prefers_the_first_unheld_executor_without_hold_noise():
    sel = ms.select_executor("implementation", availability=_HOLD_BASE)
    assert sel.executor == "opencode"
    assert "hold" not in sel.reason.lower()


def test_env_pin_still_selects_a_held_executor_with_a_noted_reason(monkeypatch):
    _stub_holds(monkeypatch, opencode="unavailable 300s ago (cooldown 60m): quota wall")
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "opencode")
    sel = ms.select_executor("implementation", availability=_HOLD_BASE)
    assert sel.executor == "opencode"  # operator override wins
    assert "pinned" in sel.reason
    assert "hold" in sel.reason.lower()


def test_all_held_abstains_with_a_reason_naming_the_holds(monkeypatch):
    _stub_holds(
        monkeypatch,
        opencode="unavailable 300s ago (cooldown 60m): quota wall",
        claude="error 60s ago (cooldown 15m): exit 1",
        manual="unavailable 10s ago (cooldown 60m): no runner",
    )
    sel = ms.select_executor("implementation", availability=_HOLD_BASE)
    assert sel.status == "ABSTAIN"
    assert sel.executor is None
    assert "held" in sel.reason.lower()
    assert "quota wall" in sel.reason
    assert "exit 1" in sel.reason


def test_abstain_lists_both_unavailable_and_held_kinds(monkeypatch):
    _stub_holds(monkeypatch, claude="error 60s ago (cooldown 15m): exit 1",
                manual="unavailable 10s ago (cooldown 60m): no runner")
    data = json.loads(json.dumps(_HOLD_BASE))
    data["executors"]["opencode"] = {"available": False, "reason": "zen key exhausted"}
    sel = ms.select_executor("implementation", availability=data)
    assert sel.status == "ABSTAIN"
    assert "zen key exhausted" in sel.reason  # the file-unavailable kind
    assert "hold" in sel.reason.lower()  # the live-hold kind, not silently ignored


def test_an_unreadable_observations_store_means_no_hold(monkeypatch):
    def boom(executor, **kwargs):
        raise RuntimeError("store on fire")
    monkeypatch.setattr(ms.model_observations, "recent_status", boom)
    sel = ms.select_executor("implementation", availability=_HOLD_BASE)
    assert sel.executor == "opencode"  # fail open: garbage never blocks a dispatch


# --------------------------------------------------------------------------- #
# Todo 2013: DeepSeek retirement + OpenCode Go tier (committed-default pins)
# --------------------------------------------------------------------------- #

def test_committed_default_routes_implementation_to_opencode_with_go_tier():
    """Fixture proving the selector picks the now-correct setup, not the stale
    one: opencode first for implementation, with a quota-priced models_go tier
    distinct from metered zen — and no deepseek-v4-pro anywhere in zen."""
    data = ms.load_availability(ms._REPO_DEFAULT)
    sel = ms.select_executor("implementation", availability=data)
    assert sel.status == "SELECTED"
    assert sel.executor == "opencode"
    go = data["executors"]["opencode"]["models_go"]
    assert go and any("deepseek-v4" in str(m) for m in go)
    paid = data["executors"]["opencode"]["models_paid_via_zen"]
    assert not any("deepseek-v4-pro" in str(m) for m in paid)
    hints = data["roles"]["implementation"]["model"]
    assert "flash" in hints["opencode_go"].lower()
    assert "pro" not in hints["opencode_go"].lower()


def test_committed_default_cost_policy_names_go_quota_and_nous_ceiling_gap():
    """The cost policy must carry the Go quota shape (preferred ahead of
    metered zen), the Nous $20 balance with its unenforceable-ceiling TODO,
    and the dated DeepSeek retirement — with Antigravity deferred, not wired."""
    data = ms.load_availability(ms._REPO_DEFAULT)
    policy = data["cost_policy"]
    assert "quota" in policy.lower()
    assert "$20" in policy
    assert "TODO" in policy
    assert "2026-09-14" in policy
    assert "Antigravity" in policy
    assert "antigravity" not in {e.lower() for e in data["executors"]}


# --------------------------------------------------------------------------- #
# Todo 2014: OpenCode Go tier wired into the implementation role
# --------------------------------------------------------------------------- #
# Semantics, verified against model_selector + harness_session before wiring:
# prefer lists name *executors* (validated against the coding-executor
# catalogue — there is deliberately no separate "opencode-go" executor entry),
# and _run_opencode passes the model id through verbatim to `opencode run -m`
# (keeping OPENCODE_GO_API_KEY and the auth.json carrying the opencode-go
# entry), so the tier is expressed purely as the model id on the "opencode"
# executor. An opencode/*-prefixed id for the same model would bill through
# zen instead — the opencode-go/* prefix is load-bearing.

_GO_BASE = {
    "updated": "2026-09-13",
    "executors": {
        "opencode": {
            "available": True,
            "models_free": ["opencode/muse-spark-1.3-contributor-free"],
            "models_go": ["opencode-go/deepseek-v4.1-flash"],
        },
        "claude": {"available": True},
        "manual": {"available": True},
    },
    "roles": {
        "implementation": {
            "prefer": ["opencode", "claude", "manual"],
            "model": {
                "opencode": "opencode-go/deepseek-v4.1-flash",
                "opencode_go": "opencode-go/deepseek-v4.1-flash",
                "opencode_zen_free": "opencode/muse-spark-1.3-contributor-free",
                "claude": "claude-sonnet-5",
            },
        },
        "review": {"prefer": ["claude", "opencode", "manual"]},
    },
}


def test_implementation_selects_go_tier_over_free_zen_when_both_available():
    """With the Go credential path present (opencode available, models_go
    listed), an implementation-role dispatch resolves to the opencode executor
    on the Go-tier id — ahead of the free zen default and ahead of claude."""
    sel = ms.select_executor("implementation", availability=_GO_BASE)
    assert sel.status == "SELECTED"
    assert sel.executor == "opencode"  # the one executor carrying both tiers
    hints = _GO_BASE["roles"]["implementation"]["model"]
    assert hints["opencode"] == "opencode-go/deepseek-v4.1-flash"
    assert hints["opencode_go"] == "opencode-go/deepseek-v4.1-flash"
    # the free zen default is demoted to an explicit fallback slot, not the id
    assert hints["opencode_zen_free"] == "opencode/muse-spark-1.3-contributor-free"
    assert hints["opencode"] != hints["opencode_zen_free"]
    # claude stays the next prefer entry — escalation, not first fallback
    prefer = _GO_BASE["roles"]["implementation"]["prefer"]
    assert prefer.index("opencode") < prefer.index("claude")


def test_implementation_degrades_to_claude_when_opencode_tier_unavailable():
    """If the opencode executor is down (no Go credential, zen exhausted), the
    implementation role still falls through to claude — the escalation path is
    preserved, just no longer the first fallback from a tiny free model."""
    data = json.loads(json.dumps(_GO_BASE))
    data["executors"]["opencode"] = {"available": False, "reason": "no Go credential and zen exhausted"}
    sel = ms.select_executor("implementation", availability=data)
    assert sel.status == "SELECTED"
    assert sel.executor == "claude"


def test_committed_default_implementation_prefers_go_id():
    """Pin the real file: the implementation role's opencode dispatch id is the
    live-verified Go-tier id, the free zen id is a named fallback, every
    models_go id carries the billing-correct opencode-go/ prefix, and prefer
    still names the single opencode executor (no separate executor entry)."""
    data = ms.load_availability(ms._REPO_DEFAULT)
    sel = ms.select_executor("implementation", availability=data)
    assert sel.status == "SELECTED"
    assert sel.executor == "opencode"
    hints = data["roles"]["implementation"]["model"]
    assert hints["opencode"] == "opencode-go/deepseek-v4.1-flash"
    assert hints["opencode_go"] == "opencode-go/deepseek-v4.1-flash"
    assert hints["opencode_zen_free"] == "opencode/muse-spark-1.3-contributor-free"
    go = data["executors"]["opencode"]["models_go"]
    assert go and all(str(m).startswith("opencode-go/") for m in go)
    assert "opencode-go/deepseek-v4.1-flash" in go
    assert data["roles"]["implementation"]["prefer"] == ["opencode", "claude", "manual"]
