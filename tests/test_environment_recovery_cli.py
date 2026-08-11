from __future__ import annotations

import argparse
from pathlib import Path

from tgw.environment_recovery_cli import run

ROOT = Path(__file__).parents[1]


def test_query_cli_returns_cited_current_fact_without_effects():
    result = run(argparse.Namespace(
        operation="query",
        registry=ROOT / "config/environment/registry.yaml",
        contract=ROOT / "config/environment/actors/tgw-steward.json",
        kind="host",
        identity="production",
    ))
    assert result["facts"]["canonical_name"] == "tgw-prod"
    assert result["effects_permitted"] is False
    assert result["citations"]


def test_query_cli_refuses_retired_host_without_redirect():
    result = run(argparse.Namespace(
        operation="query",
        registry=ROOT / "config/environment/registry.yaml",
        contract=ROOT / "config/environment/actors/tgw-steward.json",
        kind="host",
        identity="a1131",
    ))
    assert result["result"] == "refused-retired"
    assert result["facts"] == {}


def test_audit_cli_reports_incomplete_program_without_external_actions():
    result = run(argparse.Namespace(
        operation="audit", root=ROOT, observed_at="2026-08-11T10:05:00-07:00",
    ))
    assert result["counts"] == {"proved": 4, "missing": 7, "failed": 0}
    assert result["complete"] is False
    assert result["external_actions_performed"] is False
