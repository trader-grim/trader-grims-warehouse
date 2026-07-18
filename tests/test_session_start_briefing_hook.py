"""PP-RUNNERCOMMS-001 / PP-AGENT-DISCIPLINE-001 — tests for the SessionStart
briefing hook's inbox-count surfacing, generalized 2026-07-17 (todo #1484)
from being hardcoded to inbox/claude/ to working for any actor via
`TGW_HOOK_ACTOR`, plus a filenames-omitted count for every other actor's
mailbox.

Runs the hook script as a real subprocess (that's how Claude Code actually
invokes it) against a throwaway repo layout pointed at by
`TGW_HOOK_REPO_ROOT`, so no live Plan Vault state leaks into assertions.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK_PATH = Path(__file__).resolve().parents[1] / ".claude/hooks/session-start-briefing.py"


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    inbox = repo / "docs/TGW-Plan-Vault/inbox"
    for actor in ("claude", "tigwa", "dave"):
        (inbox / actor).mkdir(parents=True)
    (inbox / "archive").mkdir(parents=True)
    (inbox / "queued").mkdir(parents=True)
    (repo / "docs/TGW-Plan-Vault/suggestions").mkdir(parents=True)
    (repo / "docs/TGW-Plan-Vault/suggestions/SUGGESTIONS.md").write_text(
        "- [x] done item\n- [ ] pending item\n", encoding="utf-8"
    )
    return repo


def _run_hook(repo: Path, actor: str | None = None) -> str:
    env = {"TGW_HOOK_REPO_ROOT": str(repo), "PATH": "/usr/bin:/bin"}
    if actor is not None:
        env["TGW_HOOK_ACTOR"] = actor
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input="{}",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    return payload["hookSpecificOutput"]["additionalContext"]


def test_defaults_to_claude_actor_with_full_filename_listing(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "docs/TGW-Plan-Vault/inbox/claude/NOTE-one.md").write_text("x", encoding="utf-8")
    (repo / "docs/TGW-Plan-Vault/inbox/claude/NOTE-two.md").write_text("x", encoding="utf-8")

    out = _run_hook(repo)

    assert "### inbox/claude/ -- 2 file(s) pending" in out
    assert "NOTE-one.md" in out
    assert "NOTE-two.md" in out


def test_generalizes_to_a_second_actor_via_env_var(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "docs/TGW-Plan-Vault/inbox/tigwa/NOTE-a.md").write_text("x", encoding="utf-8")
    (repo / "docs/TGW-Plan-Vault/inbox/tigwa/NOTE-b.md").write_text("x", encoding="utf-8")
    (repo / "docs/TGW-Plan-Vault/inbox/tigwa/NOTE-c.md").write_text("x", encoding="utf-8")

    out = _run_hook(repo, actor="tigwa")

    assert "### inbox/tigwa/ -- 3 file(s) pending" in out
    assert "NOTE-a.md" in out


def test_other_actors_surfaced_as_counts_only_not_filenames(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "docs/TGW-Plan-Vault/inbox/tigwa/SECRET-note.md").write_text("x", encoding="utf-8")
    (repo / "docs/TGW-Plan-Vault/inbox/dave/OTHER-note.md").write_text("x", encoding="utf-8")

    out = _run_hook(repo)  # defaults to claude

    assert "inbox/tigwa/ -- 1 file(s) pending" in out
    assert "inbox/dave/ -- 1 file(s) pending" in out
    # Never leak another actor's filenames into this actor's own context.
    assert "SECRET-note.md" not in out
    assert "OTHER-note.md" not in out


def test_reserved_holding_dirs_excluded_from_other_actor_counts(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "docs/TGW-Plan-Vault/inbox/archive/OLD-note.md").write_text("x", encoding="utf-8")
    (repo / "docs/TGW-Plan-Vault/inbox/queued/QUEUED-note.md").write_text("x", encoding="utf-8")

    out = _run_hook(repo)

    assert "inbox/archive/" not in out
    assert "inbox/queued/" not in out


def test_empty_own_inbox_reports_empty(tmp_path):
    repo = _make_repo(tmp_path)
    out = _run_hook(repo)
    assert "### inbox/claude/ -- empty, nothing pending" in out


def test_suggestions_unchecked_count_surfaced(tmp_path):
    repo = _make_repo(tmp_path)
    out = _run_hook(repo)
    assert "SUGGESTIONS.md -- 1 unprocessed item(s)" in out
