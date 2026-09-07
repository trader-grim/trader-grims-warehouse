"""The harness onboarding canary — LEAF-11-9 W3.

Non-DB tests cover the SKIP semantics of the free-model and live tiers. The
offline end-to-end test is DB-gated (it drives the real orchestrator + ledger),
like the other harness_orchestrator tests.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tgw.development import harness_canary


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e", *args],
        cwd=cwd, check=True, text=True, capture_output=True,
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
             "PATH": "/usr/bin:/bin", "HOME": str(cwd)},
    ).stdout.strip()


# --------------------------------------------------------------------------- #
# SKIP semantics (no DB, no dispatch)
# --------------------------------------------------------------------------- #

def test_free_model_tier_skips_without_a_reachable_route(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed").write_text("0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")

    monkeypatch.setattr(harness_canary, "_free_model", lambda: None)
    row = harness_canary.run_tier(
        "free-model", repository=repo, coder_user=None, python=sys.executable,
        worktree_root=tmp_path / "wt",
    )
    assert row["result"] == "skipped"
    assert "free" in row["reason"]


def test_free_model_tier_records_model_but_skips_dispatch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed").write_text("0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")

    monkeypatch.setattr(
        harness_canary, "_free_model",
        lambda: {"provider": "groq", "model_id": "groq/llama-x:free"},
    )
    row = harness_canary.run_tier(
        "free-model", repository=repo, coder_user=None, python=sys.executable,
        worktree_root=tmp_path / "wt",
    )
    assert row["result"] == "skipped"
    assert row["free_model"]["model_id"] == "groq/llama-x:free"
    assert "no free-model executor runner wired" in row["reason"]


def test_live_tier_skips_without_a_verified_paid_credential(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed").write_text("0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")

    from tgw import coding_executor_catalog
    monkeypatch.setattr(coding_executor_catalog, "executor_specs", lambda **_k: {})
    row = harness_canary.run_tier(
        "live", repository=repo, coder_user=None, python=sys.executable,
        worktree_root=tmp_path / "wt",
    )
    assert row["result"] == "skipped"
    assert "paid executor credential" in row["reason"]


def test_main_returns_zero_for_a_skip(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed").write_text("0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")

    monkeypatch.setattr(harness_canary.harness_ledger, "init", lambda *_a, **_k: None)
    monkeypatch.setattr(harness_canary, "_state_dsn", lambda _o: "dbname=x")
    monkeypatch.setattr(harness_canary, "_free_model", lambda: None)
    code = harness_canary.main([
        "--tier", "free-model", "--repository", str(repo),
        "--worktree-root", str(tmp_path / "wt"), "--coder-user", "",
    ])
    assert code == 0
    assert '"result": "skipped"' in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# offline end-to-end — DB-gated
# --------------------------------------------------------------------------- #

_CANDIDATE_DSNS = (
    os.environ.get("TGW_TEST_STATE_MACHINE_DSN"),
    "dbname=tgw_lib_dev_state_machine user=tgw_coding",
)


def _reachable_dsn():
    import psycopg2
    for dsn in _CANDIDATE_DSNS:
        if not dsn:
            continue
        try:
            with psycopg2.connect(dsn) as c, c.cursor() as cur:
                cur.execute("SELECT 1")
            return dsn
        except Exception:
            continue
    return None


DSN = _reachable_dsn()


@pytest.mark.skipif(DSN is None, reason="no PostgreSQL reachable for the harness ledger")
def test_offline_tier_lands_then_is_idempotent(tmp_path):
    from tgw.development import harness_ledger

    harness_ledger.init(DSN)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed").write_text("0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")

    created: list[str] = []
    try:
        first = harness_canary.run_tier(
            "offline", repository=repo, coder_user=None, python=sys.executable,
            worktree_root=tmp_path / "wt",
        )
        created.append(first["task_id"])
        assert first["result"] == "landed", first
        assert _git(repo, "show", "main:.tgw-canary").startswith("harness offline canary")
        assert _git(repo, "branch", "--list", "coding/*") == ""

        second = harness_canary.run_tier(
            "offline", repository=repo, coder_user=None, python=sys.executable,
            worktree_root=tmp_path / "wt",
        )
        created.append(second["task_id"])
        assert second["result"] == "already_satisfied", second
        assert len(_git(repo, "log", "--oneline").splitlines()) == 2
    finally:
        import psycopg2
        with psycopg2.connect(DSN) as c, c.cursor() as cur:
            cur.execute("DELETE FROM harness_ledger_task WHERE task_id = ANY(%s)", (created,))
