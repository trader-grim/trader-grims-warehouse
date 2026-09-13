"""tgw.model_availability_refresh — the daily freshness loop (LEAF-11-8 / Todo 1956)."""

from __future__ import annotations

import json

import pytest

from tgw import model_availability_refresh as mar

pytestmark = pytest.mark.usefixtures("_pin_coding_executors")


def _write(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _base_data():
    return {
        "_comment": "Semi-automatic model/executor selection — hand-maintained until the prober lands.",
        "schema": "tgw-model-availability/v1",
        "updated": "2026-09-04",
        "role_chart_source": "reference/research/source/catio-model-role-research-20260827.md",
        "cost_policy": "Preserve the operator Claude subscription for judgement roles.",
        "executors": {
            "claude": {
                "available": True,
                "harness": "claude-code CLI",
                "models": ["claude-sonnet-5", "claude-opus-5"],
                "note": "operator subscription note",
            },
            "opencode": {
                "available": True,
                "harness": "opencode CLI",
                "models_free": ["opencode/muse-spark-1.3-contributor-free"],
                "models_paid_via_zen": ["opencode/gpt-5.6-sol"],
            },
            "manual": {
                "available": True,
                "harness": "a supervising tgw-coders session; always available, last resort",
            },
            "frozen_exec": {
                "available": False,
                "reason": "manually pinned off",
                "freshness": "frozen",
            },
        },
        "roles": {
            "implementation": {
                "prefer": ["opencode", "claude", "manual"],
                "model": {
                    "opencode": "opencode/stale-model",
                    "claude": "claude-sonnet-5",
                },
                "consumed_by_selector": True,
                "research": "role 4 research note",
                "note": "opencode-first is intentional cost control",
            },
            "review": {
                "prefer": ["claude", "opencode", "manual"],
                "model": {"claude": "claude-sonnet-5"},
                "consumed_by_selector": True,
                "research": "role 6 research note",
            },
            "orchestrator": {
                "prefer": ["claude", "opencode", "manual"],
                "model": {"claude": "claude-opus-5"},
                "consumed_by_selector": False,
                "research": "role 1 research note",
            },
            "frozen_role": {
                "prefer": ["claude", "manual"],
                "model": {"claude": "totally-made-up-model"},
                "consumed_by_selector": False,
                "freshness": "frozen",
                "research": "should never move",
            },
        },
    }


def _fake_live(models):
    def _fn(*, timeout=10.0):
        return {
            "models": models,
            "offline": not models,
            "sources_live": ["openrouter"] if models else [],
            "sources_failed": [] if models else ["openrouter: timed out"],
        }
    return _fn


_LIVE_MODELS = [
    {"provider": "opencode", "model_id": "opencode/muse-spark-1.3-contributor-free", "context": 262144, "tool_use": True, "free": True},
    {"provider": "opencode", "model_id": "opencode/claude-sonnet-5", "context": 200000, "tool_use": True, "free": False},
    {"provider": "openrouter", "model_id": "anthropic/claude-sonnet-5", "context": 200000, "tool_use": True, "free": False},
]


def test_offline_adapter_returns_nothing_no_changes_receipt_notes_failure(tmp_path, monkeypatch, durable_path):
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live([]))
    path = _write(tmp_path / "model-availability.json", _base_data())
    before = json.loads(path.read_text(encoding="utf-8"))

    receipt = mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["executors"] == before["executors"]
    assert after["roles"] == before["roles"]
    assert receipt["sources_failed"]
    assert receipt["sources_live"] == []
    assert receipt["changed"] == []
    assert receipt["written"] is True


def test_prefer_arrays_and_comment_research_note_strings_preserved(tmp_path, monkeypatch, durable_path):
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(_LIVE_MODELS))
    data = _base_data()
    path = _write(tmp_path / "model-availability.json", data)

    mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["_comment"] == data["_comment"]
    assert after["role_chart_source"] == data["role_chart_source"]
    assert after["cost_policy"] == data["cost_policy"]
    for role_name, role in data["roles"].items():
        assert after["roles"][role_name]["prefer"] == role["prefer"]
        if "research" in role:
            assert after["roles"][role_name]["research"] == role["research"]
        if "note" in role:
            assert after["roles"][role_name]["note"] == role["note"]
    assert after["executors"]["claude"]["note"] == data["executors"]["claude"]["note"]


def test_frozen_executor_and_role_are_byte_identical(tmp_path, monkeypatch, durable_path):
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(_LIVE_MODELS))
    data = _base_data()
    path = _write(tmp_path / "model-availability.json", data)

    mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["executors"]["frozen_exec"] == data["executors"]["frozen_exec"]
    assert after["roles"]["frozen_role"] == data["roles"]["frozen_role"]


def test_stale_model_hint_on_non_selector_role_is_swapped_and_recorded(tmp_path, monkeypatch, durable_path):
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(_LIVE_MODELS))
    data = _base_data()
    path = _write(tmp_path / "model-availability.json", data)

    receipt = mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    # "claude-opus-5" is not in the live catalogue -> migrated to the best live
    # candidate for the "claude" *family specifically* (the bare id on
    # executors.claude.models — "claude-sonnet-5", reachable here via both the
    # opencode-zen route and openrouter), never a catalogue-wide best pick
    # from an unrelated family.
    assert after["roles"]["orchestrator"]["model"]["claude"] == "claude-sonnet-5"
    migrations = [c for c in receipt["changed"] if c.get("role") == "orchestrator"]
    assert migrations == [{
        "role": "orchestrator", "slot": "claude",
        "was": "claude-opus-5", "now": "claude-sonnet-5",
        "reason": "not in live catalogue",
    }]


def test_stale_migration_never_picks_a_model_outside_the_slots_family(tmp_path, monkeypatch, durable_path):
    """Regression for the bug where migration replaced ANY stale hint with the
    single globally-top-ranked live model, regardless of family."""
    live_models_opencode_only = [
        {"provider": "opencode", "model_id": "opencode/muse-spark-1.3-contributor-free", "context": 262144, "tool_use": True, "free": True},
    ]
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(live_models_opencode_only))
    data = _base_data()
    path = _write(tmp_path / "model-availability.json", data)

    receipt = mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    # No live "claude" family candidate exists (opencode-only catalogue) -> the
    # stale claude hint is left as written rather than migrated to an
    # unrelated opencode model.
    assert after["roles"]["orchestrator"]["model"]["claude"] == "claude-opus-5"
    assert [c for c in receipt["changed"] if c.get("role") == "orchestrator"] == []


def test_claude_slot_restricted_to_closed_executor_model_list(tmp_path, monkeypatch, durable_path):
    """Regression for the bug where a 'claude' slot matched any live model
    whose id/provider merely contained the substring 'claude' (e.g. an
    OpenRouter-routed 'anthropic/claude-fable-5.1'), not restricted to
    executors.claude.models (the closed list the claude-code CLI accepts)."""
    live_models = [
        {"provider": "openrouter", "model_id": "anthropic/claude-fable-5.1", "context": 200000, "tool_use": True, "free": False},
    ]
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(live_models))
    data = _base_data()
    path = _write(tmp_path / "model-availability.json", data)

    receipt = mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    # "claude-fable-5.1" is not on executors.claude.models, so it is never a
    # valid candidate for a claude slot -> the stale hint is left untouched.
    assert after["roles"]["orchestrator"]["model"]["claude"] == "claude-opus-5"
    assert [c for c in receipt["changed"] if c.get("role") == "orchestrator"] == []


def test_selector_role_still_live_hints_are_not_re_optimised(tmp_path, monkeypatch, durable_path):
    # a consumed_by_selector role whose hints are all still live is left
    # untouched — the refresh does NOT second-guess the role chart's model
    # choice just because another live model outranks it. Only stale hints move.
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(_LIVE_MODELS))
    data = _base_data()
    data["roles"]["implementation"]["model"] = {
        "opencode": "opencode/muse-spark-1.3-contributor-free",  # still live
        "claude": "claude-sonnet-5",                             # still live, not top-ranked
    }
    path = _write(tmp_path / "model-availability.json", data)

    receipt = mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["roles"]["implementation"]["model"] == {
        "opencode": "opencode/muse-spark-1.3-contributor-free",
        "claude": "claude-sonnet-5",
    }
    assert [c for c in receipt["changed"] if c.get("role") == "implementation"] == []


def test_selector_role_stale_hint_still_migrates(tmp_path, monkeypatch, durable_path):
    # conservative != frozen: a consumed_by_selector role whose hint names a
    # model no longer in the live catalogue is still migrated.
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(_LIVE_MODELS))
    data = _base_data()
    data["roles"]["implementation"]["model"] = {
        "opencode": "opencode/retired-model",
        "claude": "claude-sonnet-5",
    }
    path = _write(tmp_path / "model-availability.json", data)

    receipt = mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["roles"]["implementation"]["model"]["opencode"] != "opencode/retired-model"
    assert after["roles"]["implementation"]["model"]["claude"] == "claude-sonnet-5"
    moved = [c for c in receipt["changed"] if c.get("role") == "implementation" and c.get("slot") == "opencode"]
    assert moved and moved[0]["reason"] == "not in live catalogue"


def test_prefixed_hint_matching_a_bare_live_id_is_not_treated_as_stale(tmp_path, monkeypatch, durable_path):
    # the live source reports "muse-spark-1.3-contributor-free"; the hint says
    # "opencode/muse-spark-1.3-contributor-free" — same model, must not churn.
    live = [{"provider": "opencode", "model_id": "muse-spark-1.3-contributor-free",
             "context": 262144, "tool_use": True, "free": True}]
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(live))
    data = _base_data()
    data["roles"]["implementation"]["model"] = {"opencode": "opencode/muse-spark-1.3-contributor-free"}
    path = _write(tmp_path / "model-availability.json", data)

    receipt = mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["roles"]["implementation"]["model"]["opencode"] == "opencode/muse-spark-1.3-contributor-free"
    assert [c for c in receipt["changed"] if c.get("role") == "implementation"] == []


def test_dry_run_writes_nothing(tmp_path, monkeypatch, durable_path):
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(_LIVE_MODELS))
    data = _base_data()
    path = _write(tmp_path / "model-availability.json", data)
    original_bytes = path.read_bytes()
    receipts_path = durable_path / "receipts.jsonl"

    receipt = mar.refresh(path=path, dry_run=True, receipts_path=receipts_path)

    assert path.read_bytes() == original_bytes
    assert receipt["written"] is False
    assert not receipts_path.exists()
    # the dry run still computes the diff
    assert receipt["changed"]


def test_updated_date_bumped(tmp_path, monkeypatch, durable_path):
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(_LIVE_MODELS))
    data = _base_data()
    path = _write(tmp_path / "model-availability.json", data)

    mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["updated"] != "2026-09-04"


def test_module_cli_entrypoint_dry_run(tmp_path, monkeypatch, durable_path, capsys):
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live([]))
    monkeypatch.setattr(mar, "_RECEIPTS_PATH", durable_path / "receipts.jsonl")
    data = _base_data()
    path = _write(tmp_path / "model-availability.json", data)

    code = mar.main(["--dry-run", "--path", str(path)])

    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["schema"] == mar.SCHEMA
    assert printed["written"] is False


def test_receipt_appended_to_durable_receipts_file(tmp_path, monkeypatch, durable_path):
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(_LIVE_MODELS))
    data = _base_data()
    path = _write(tmp_path / "model-availability.json", data)
    receipts_path = durable_path / "nested" / "receipts.jsonl"

    receipt = mar.refresh(path=path, receipts_path=receipts_path)

    lines = receipts_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["schema"] == mar.SCHEMA == receipt["schema"]


def test_models_go_list_counts_as_a_provider_path(tmp_path, monkeypatch, durable_path):
    """The quota-priced OpenCode Go tier (models_go, added 2026-09-12) rides the
    same opencode CLI as zen, so it is a list on the existing entry — and the
    refresh must treat it as a provider path like the other three lists."""
    live = [
        {"provider": "opencode", "model_id": "opencode/deepseek-v4.1-flash",
         "context": 262144, "tool_use": True, "free": False},
    ]
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(live))
    data = _base_data()
    data["executors"]["opencode"] = {
        "available": False,
        "reason": "stale reason",
        "harness": "opencode CLI",
        "models_go": ["opencode/deepseek-v4.1-flash"],
    }
    path = _write(tmp_path / "model-availability.json", data)

    receipt = mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["executors"]["opencode"]["available"] is True
    assert "reason" not in after["executors"]["opencode"]
    assert [c for c in receipt["changed"] if c.get("executor") == "opencode"]


def test_committed_default_prefers_flash_over_retired_v4_pro():
    """Todo 2013: DeepSeek v4-Pro is retired 2026-09-14 (requests route to
    v4.1-Flash at Flash rates) and v4.1-Flash now benchmarks above it — the
    committed default must rank Flash first, not Pro."""
    from pathlib import Path

    repo_default = Path(__file__).resolve().parent.parent / "config" / "model-availability.json"
    data = json.loads(repo_default.read_text(encoding="utf-8"))

    paid = data["executors"]["opencode"]["models_paid_via_zen"]
    assert not any("deepseek-v4-pro" in str(m) for m in paid)
    go = data["executors"]["opencode"]["models_go"]
    assert go and any("deepseek-v4" in str(m) for m in go)
    research_bits = [
        data["roles"]["implementation"]["research"],
        data["roles"]["evidence_worker"]["research"],
    ]
    assert "best DeepSeek V4 Pro" not in data["roles"]["evidence_worker"]["research"]
    assert "least DeepSeek V4 Pro" not in data["roles"]["implementation"]["research"]
    assert all("V4.1 Flash" in r for r in research_bits)
    assert "2026-09-14" in data["cost_policy"]
    assert "opencode_go" in data["roles"]["implementation"]["model"]
    assert "groq" in json.dumps(data["roles"]["implementation"]["model"]).lower()


# --------------------------------------------------------------------------- #
# Todo 2014: OpenCode Go tier survives the daily refresh
# --------------------------------------------------------------------------- #

_LIVE_WITHOUT_GO = [
    {"provider": "opencode", "model_id": "opencode/muse-spark-1.3-contributor-free", "context": 262144, "tool_use": True, "free": True},
]


def _go_executor():
    return {
        "available": True,
        "harness": "opencode CLI",
        "models_free": ["opencode/muse-spark-1.3-contributor-free"],
        "models_go": ["opencode-go/deepseek-v4.1-flash"],
    }


def test_go_tier_hint_on_models_go_slice_is_not_migrated(tmp_path, monkeypatch, durable_path):
    """The live sources structurally cannot discover opencode-go/* ids, so the
    refresher must treat a hint matching the committed models_go slice as live
    — otherwise the daily job would migrate the Go tier away on every run."""
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(_LIVE_WITHOUT_GO))
    data = _base_data()
    data["executors"]["opencode"] = _go_executor()
    data["roles"]["implementation"]["model"] = {
        "opencode": "opencode-go/deepseek-v4.1-flash",
        "opencode_go": "opencode-go/deepseek-v4.1-flash",
        "opencode_zen_free": "opencode/muse-spark-1.3-contributor-free",
        "claude": "claude-sonnet-5",
    }
    path = _write(tmp_path / "model-availability.json", data)

    receipt = mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    impl = after["roles"]["implementation"]["model"]
    assert impl["opencode"] == "opencode-go/deepseek-v4.1-flash"
    assert impl["opencode_go"] == "opencode-go/deepseek-v4.1-flash"
    assert [c for c in receipt["changed"] if c.get("role") == "implementation"] == []


def test_go_tier_hint_missing_from_models_go_slice_still_migrates(tmp_path, monkeypatch, durable_path):
    """No freeze-forever: a Go-prefixed hint that is neither live nor on the
    committed models_go slice still migrates to the best live opencode
    candidate instead of pointing at a dead model."""
    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(_LIVE_MODELS))
    data = _base_data()
    data["executors"]["opencode"] = _go_executor()
    data["roles"]["implementation"]["model"] = {
        "opencode": "opencode-go/retired-go-model",
        "claude": "claude-sonnet-5",
    }
    path = _write(tmp_path / "model-availability.json", data)

    receipt = mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["roles"]["implementation"]["model"]["opencode"] == "opencode/muse-spark-1.3-contributor-free"
    moved = [c for c in receipt["changed"] if c.get("role") == "implementation" and c.get("slot") == "opencode"]
    assert moved and moved[0]["was"] == "opencode-go/retired-go-model"
    assert moved[0]["reason"] == "not in live catalogue"


def test_committed_default_go_hints_survive_a_go_blind_refresh(tmp_path, monkeypatch, durable_path):
    """End-to-end pin on the real file: with a live catalogue that (like the
    real sources) cannot see opencode-go/*, refreshing a copy of the committed
    default must leave both implementation Go-tier hints exactly as written."""
    from pathlib import Path

    monkeypatch.setattr(mar.model_currency_adapter, "live_coding_models", _fake_live(_LIVE_WITHOUT_GO))
    repo_default = Path(__file__).resolve().parent.parent / "config" / "model-availability.json"
    path = tmp_path / "model-availability.json"
    path.write_text(repo_default.read_text(encoding="utf-8"), encoding="utf-8")

    receipt = mar.refresh(path=path, receipts_path=durable_path / "receipts.jsonl")

    after = json.loads(path.read_text(encoding="utf-8"))
    impl = after["roles"]["implementation"]["model"]
    assert impl["opencode"] == "opencode-go/deepseek-v4.1-flash"
    assert impl["opencode_go"] == "opencode-go/deepseek-v4.1-flash"
    touched_go_slots = [
        c for c in receipt["changed"]
        if c.get("role") == "implementation" and c.get("slot") in ("opencode", "opencode_go")
    ]
    assert touched_go_slots == []
