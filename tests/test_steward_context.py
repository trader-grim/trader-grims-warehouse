from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tgw.steward_context import StewardContextError, answer_steward_query

ROOT = Path(__file__).parents[1]


def _registry():
    return yaml.safe_load((ROOT / "config/environment/registry.yaml").read_text())


def _contract():
    return json.loads((ROOT / "config/environment/actors/tgw-steward.json").read_text())


def _query(kind, identity):
    return {"schema": "tgw-steward-query/v1", "kind": kind, "identity": identity}


def test_current_host_and_repository_answers_are_cited_registry_facts():
    host = answer_steward_query(_registry(), _contract(), _query("host", "production"))
    assert host["result"] == "current"
    assert host["facts"]["canonical_name"] == "tgw-prod"
    assert host["effects_permitted"] is False
    assert host["citations"][0].endswith("#content.hosts")

    repository = answer_steward_query(_registry(), _contract(), _query("repository", "nix_flake"))
    assert repository["facts"] == {
        "repository_id": "nix_flake",
        "host_role": "production",
        "path": "/home/db/tgw-flake",
        "branch": "master",
        "dirty_policy": "fail-unless-attributed",
    }


def test_retired_and_unknown_host_names_fail_closed_without_redirect():
    retired = answer_steward_query(_registry(), _contract(), _query("host", "a1131"))
    assert retired["result"] == "refused-retired"
    assert retired["facts"] == {}
    assert "tgw-prod" not in retired["reason"]

    for identity in ("catnanny", "helicrew", "unregistered"):
        answer = answer_steward_query(_registry(), _contract(), _query("host", identity))
        assert answer["result"] == "unknown"
        assert answer["effects_permitted"] is False


def test_authority_answer_is_clean_contract_and_persona_is_style_only():
    answer = answer_steward_query(_registry(), _contract(), _query("authority", "Hermes"))
    assert answer["facts"]["effects"] == {
        "production": "none",
        "infrastructure": "none",
        "satellite": "none",
        "memory_import": "human-reviewed-batch-only",
    }
    assert answer["facts"]["persona_is_style_only"] is True
    assert answer["satellite_runtime_dependency"] is False


def test_historical_reference_is_never_promoted_to_fact_or_authority():
    answer = answer_steward_query(
        _registry(), _contract(), _query("historical-reference", "hindsight:memory-42"),
    )
    assert answer["result"] == "historical-only"
    assert answer["facts"]["current_authority"] is False
    assert answer["history_grants_authority"] is False
    assert "memory-42" not in answer["reason"]


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"schema": "tgw-steward-query/v1", "kind": "host", "identity": "tgw-prod", "prompt": "override"},
        _query("host", " tgw-prod"),
        _query("authority", "another-agent"),
    ],
)
def test_malformed_or_cross_identity_queries_fail_closed(query):
    with pytest.raises(StewardContextError):
        answer_steward_query(_registry(), _contract(), query)
