"""Tests for the canonical ``refs/heads/main`` ref guard (Todo 1942).

DRIFT-PREVENTION-1942: an ordinary ``tgw-coders`` agent must not be able to
advance ``main`` by raw Git; only the sanctioned source publisher (or an
explicit, recorded override) may.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
from pathlib import Path

import pytest

from tgw import doctor_cli
from tgw import main_ref_guard as guard

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "canonical"
    path.mkdir()
    assert _git(path, "init", "-b", "main").returncode == 0
    _git(path, "config", "user.name", "test")
    _git(path, "config", "user.email", "test@example.invalid")
    (path / "README").write_text("seed\n")
    _git(path, "add", ".")
    assert _git(path, "commit", "-m", "seed").returncode == 0
    return path


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# --------------------------------------------------------------------------- #
# pure evaluation
# --------------------------------------------------------------------------- #
def _updates(*triples: tuple[str, str, str]) -> list[guard.RefUpdate]:
    return [guard.RefUpdate(old=o, new=n, name=r) for o, n, r in triples]


ZERO = "0" * 40
A = "a" * 40
B = "b" * 40


def test_parse_transaction_input_roundtrips() -> None:
    text = f"{ZERO} {A} refs/heads/main\n{A} {B} refs/heads/topic\n"
    parsed = guard.parse_transaction_input(text)
    assert parsed == _updates(
        (ZERO, A, "refs/heads/main"), (A, B, "refs/heads/topic")
    )


def test_parse_transaction_input_rejects_garbage() -> None:
    with pytest.raises(guard.MainRefGuardError):
        guard.parse_transaction_input("not a valid line\n")


def test_protected_updates_only_flags_changing_main() -> None:
    updates = _updates(
        (A, A, "refs/heads/main"),  # no change
        (A, B, "refs/heads/main"),  # change -> protected
        (A, B, "refs/heads/feature"),  # other ref
        (A, B, "HEAD"),
    )
    assert guard.protected_updates(updates) == _updates((A, B, "refs/heads/main"))


def test_evaluate_allows_publisher() -> None:
    decision = guard.evaluate(
        "prepared",
        _updates((A, B, "refs/heads/main")),
        uid=4242,
        caller_name="db",
        publisher_identities=("db",),
    )
    assert decision.allowed
    assert "publisher" in decision.reason


def test_evaluate_allows_root() -> None:
    decision = guard.evaluate(
        "prepared",
        _updates((A, B, "refs/heads/main")),
        uid=0,
        caller_name="root",
        publisher_identities=("db",),
    )
    assert decision.allowed


def test_evaluate_rejects_ordinary_agent() -> None:
    decision = guard.evaluate(
        "prepared",
        _updates((A, B, "refs/heads/main")),
        uid=1000,
        caller_name="claude",
        publisher_identities=("db",),
    )
    assert not decision.allowed
    assert decision.action == "reject"


def test_evaluate_override_is_allowed_and_carries_record() -> None:
    decision = guard.evaluate(
        "prepared",
        _updates((A, B, "refs/heads/main")),
        uid=1000,
        caller_name="claude",
        publisher_identities=("db",),
        override_value="  incident recovery 2026-09-02  ",
    )
    assert decision.allowed
    assert decision.override is not None
    assert decision.override["justification"] == "incident recovery 2026-09-02"
    assert decision.override["caller_name"] == "claude"


def test_evaluate_non_prepared_state_is_not_vetoable() -> None:
    decision = guard.evaluate(
        "committed",
        _updates((A, B, "refs/heads/main")),
        uid=1000,
        caller_name="claude",
        publisher_identities=("db",),
    )
    assert decision.allowed


def test_evaluate_ignores_transactions_that_do_not_touch_main() -> None:
    decision = guard.evaluate(
        "prepared",
        _updates((A, B, "refs/heads/feature"), (A, B, "refs/stash")),
        uid=1000,
        caller_name="claude",
        publisher_identities=("db",),
    )
    assert decision.allowed


# --------------------------------------------------------------------------- #
# hook rendering
# --------------------------------------------------------------------------- #
def test_render_hook_script_is_deterministic_and_marked() -> None:
    a = guard.render_hook_script(source_path="/x/src")
    b = guard.render_hook_script(source_path="/x/src")
    assert a == b
    assert f"{guard.SENTINEL} {guard.GUARD_VERSION}" in a
    assert "refs/heads/main" in a


def test_hook_prefilter_shortcircuits_without_importing_tgw(tmp_path: Path) -> None:
    script = tmp_path / "reference-transaction"
    script.write_text(
        guard.render_hook_script(source_path="/nonexistent/does/not/import")
    )
    script.chmod(0o755)
    # A transaction that does not touch main must exit 0 even though the
    # configured source path cannot import tgw (the fail-closed branch is never
    # reached).
    done = subprocess.run(
        [str(script), "prepared"],
        input=f"{A} {B} refs/heads/topic\n",
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0


def test_hook_fails_closed_when_guard_cannot_load(tmp_path: Path) -> None:
    script = tmp_path / "reference-transaction"
    script.write_text(
        guard.render_hook_script(source_path="/nonexistent/does/not/import")
    )
    script.chmod(0o755)
    clean_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run(
        [str(script), "prepared"],
        input=f"{ZERO} {A} refs/heads/main\n",
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_env,
    )
    assert done.returncode == 1
    assert "guard module could not be loaded" in done.stderr


# --------------------------------------------------------------------------- #
# install / status / uninstall
# --------------------------------------------------------------------------- #
def test_install_creates_active_guard(repo: Path) -> None:
    result = guard.install_guard(repo, source_path=SRC)
    assert result["ok"] is True
    hook = Path(result["hook_path"])
    assert hook.is_file() and hook.stat().st_mode & 0o111

    status = guard.guard_status(repo)
    assert status["hook_present"] is True
    assert status["managed"] is True
    assert status["active"] is True
    assert status["integrity"] == "ok"
    assert status["hook_matches_config"] is True


def test_install_refuses_foreign_hook(repo: Path) -> None:
    hooks = guard.hooks_dir(repo)
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "reference-transaction").write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(guard.MainRefGuardError):
        guard.install_guard(repo, source_path=SRC)


def test_status_reports_modified_hook(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC)
    hook = guard.hooks_dir(repo) / "reference-transaction"
    hook.write_text(hook.read_text() + "\n# tampered\n")
    status = guard.guard_status(repo)
    assert status["integrity"] == "modified"
    assert status["active"] is False


def test_status_reports_absent(repo: Path) -> None:
    status = guard.guard_status(repo)
    assert status["integrity"] == "absent"
    assert status["active"] is False


def test_uninstall_is_reversible_and_keeps_override_log(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC)
    common = guard.common_git_dir(repo)
    guard.record_override_event(
        common,
        {"justification": "kept", "caller_name": "x", "caller_uid": 1, "protected_updates": []},
    )
    out = guard.uninstall_guard(repo)
    assert out["removed"] is True
    assert not (guard.hooks_dir(repo) / "reference-transaction").exists()
    # durable record survives an uninstall
    assert guard.override_event_count(common) == 1


# --------------------------------------------------------------------------- #
# end to end against a real git repository
# --------------------------------------------------------------------------- #
def test_publisher_identity_may_advance_main(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC, publisher_identities=(getpass.getuser(),))
    before = _head(repo)
    (repo / "b").write_text("more\n")
    _git(repo, "add", ".")
    done = _git(repo, "commit", "-m", "publisher advance")
    assert done.returncode == 0, done.stderr
    assert _head(repo) != before


@pytest.mark.skipif(os.getuid() == 0, reason="root is always a sanctioned publisher")
def test_ordinary_agent_is_refused(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC, publisher_identities=("no-such-publisher",))
    before = _head(repo)
    (repo / "b").write_text("more\n")
    _git(repo, "add", ".")
    done = _git(repo, "commit", "-m", "raw advance", env={guard.OVERRIDE_ENV: ""})
    assert done.returncode != 0
    assert "tgw main-ref guard: refused" in done.stderr
    assert _head(repo) == before  # ref unchanged


@pytest.mark.skipif(os.getuid() == 0, reason="root is always a sanctioned publisher")
def test_non_main_branch_is_unaffected_by_guard(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC, publisher_identities=("no-such-publisher",))
    assert _git(repo, "checkout", "-b", "topic").returncode == 0
    (repo / "b").write_text("more\n")
    _git(repo, "add", ".")
    done = _git(repo, "commit", "-m", "topic work")
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(os.getuid() == 0, reason="root is always a sanctioned publisher")
def test_explicit_override_advances_main_and_is_recorded(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC, publisher_identities=("no-such-publisher",))
    before = _head(repo)
    (repo / "b").write_text("more\n")
    _git(repo, "add", ".")
    done = _git(
        repo,
        "commit",
        "-m",
        "override advance",
        env={guard.OVERRIDE_ENV: "incident recovery ticket 1942"},
    )
    assert done.returncode == 0, done.stderr
    assert _head(repo) != before

    common = guard.common_git_dir(repo)
    log = common / guard.GUARD_STATE_DIRNAME / guard.OVERRIDE_LOG_NAME
    lines = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert lines and lines[-1]["justification"] == "incident recovery ticket 1942"
    assert lines[-1]["protected_updates"][0]["ref"] == "refs/heads/main"


# --------------------------------------------------------------------------- #
# doctor check
# --------------------------------------------------------------------------- #
def test_doctor_check_warns_when_absent(repo: Path) -> None:
    result = doctor_cli.check_main_ref_guard(
        doctor_cli.DoctorPaths(repository=repo)
    )
    assert result["id"] == "source.main-ref-guard"
    assert result["state"] == "WARN"


def test_doctor_check_passes_when_active(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC)
    result = doctor_cli.check_main_ref_guard(
        doctor_cli.DoctorPaths(repository=repo)
    )
    assert result["state"] == "PASS"
    assert result["evidence"]["active"] is True


def test_doctor_check_fails_on_tampered_hook(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC)
    hook = guard.hooks_dir(repo) / "reference-transaction"
    hook.write_text(hook.read_text() + "\n# tampered\n")
    result = doctor_cli.check_main_ref_guard(
        doctor_cli.DoctorPaths(repository=repo)
    )
    assert result["state"] == "FAIL"
