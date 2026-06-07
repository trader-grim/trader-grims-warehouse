"""PP-PERP-AUTO-001 — tests for `tgw perp-run` brief prompt loader.

Covers the `## Prompt` parser, brief id resolution (substring + ambiguity),
list mode, and clipboard push. _push_clipboard is stubbed; no GUI involved.
"""

import pytest

import tgw.api as api

_BRIEF = """---
title: Test Brief
---

# PERPLEXITY-001 — eBay scopes

## Context
Some background that must NOT be copied.

## Prompt
Research the eBay OAuth scopes required for sold price data.
Include marketplace_insights and analytics.

## Notes
Trailing notes that must NOT be copied.
"""


@pytest.fixture
def cfg(tmp_path):
    perp = tmp_path / "perplexity"
    perp.mkdir(parents=True)
    (perp / "PERPLEXITY-001-ebay-scopes.md").write_text(_BRIEF, encoding="utf-8")
    (perp / "PERPLEXITY-002-cassini-seo.md").write_text(
        "## Prompt\nExplain Cassini ranking.\n", encoding="utf-8")
    return {"plan_vault_path": tmp_path}


@pytest.fixture(autouse=True)
def _stub_clipboard(monkeypatch):
    monkeypatch.setattr(api, "_push_clipboard", lambda text: True)


def test_parse_prompt_section_isolates_body():
    body = api._parse_prompt_section(_BRIEF)
    assert body.startswith("Research the eBay OAuth scopes")
    assert "marketplace_insights" in body
    assert "background" not in body   # ## Context excluded
    assert "Trailing notes" not in body  # ## Notes excluded


def test_list_briefs(cfg):
    out = api.cmd_perp_run(cfg, list_briefs=True)
    assert out["ok"] is True
    assert out["count"] == 2
    assert "PERPLEXITY-001-ebay-scopes" in out["briefs"]


def test_no_brief_id_lists(cfg):
    out = api.cmd_perp_run(cfg)
    assert out["ok"] is True
    assert out["count"] == 2


def test_resolve_and_load_prompt(cfg):
    out = api.cmd_perp_run(cfg, brief_id="PERPLEXITY-001")
    assert out["ok"] is True
    assert out["brief"] == "PERPLEXITY-001-ebay-scopes"
    assert out["clipboard"] is True
    assert out["prompt_chars"] > 0


def test_substring_match(cfg):
    out = api.cmd_perp_run(cfg, brief_id="cassini")
    assert out["ok"] is True
    assert out["brief"] == "PERPLEXITY-002-cassini-seo"


def test_no_match(cfg):
    out = api.cmd_perp_run(cfg, brief_id="nonexistent")
    assert out["ok"] is False
    assert "no brief matching" in out["error"]


def test_ambiguous_match(cfg):
    out = api.cmd_perp_run(cfg, brief_id="perplexity")
    assert out["ok"] is False
    assert "ambiguous" in out["error"]
    assert len(out["matches"]) == 2


def test_missing_prompt_section(cfg, tmp_path):
    (tmp_path / "perplexity" / "PERPLEXITY-003-noprompt.md").write_text(
        "# Brief\n## Context\nonly context\n", encoding="utf-8")
    out = api.cmd_perp_run(cfg, brief_id="noprompt")
    assert out["ok"] is False
    assert "no \"## Prompt\" section" in out["error"]


def test_missing_perplexity_dir():
    out = api.cmd_perp_run({"plan_vault_path": __import__("pathlib").Path("/nonexistent")})
    assert out["ok"] is False
    assert "perplexity dir not found" in out["error"]
