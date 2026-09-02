"""Tests for the canonical ``refs/heads/main`` ref guard (Todo 1942).

DRIFT-PREVENTION-1942: an ordinary ``tgw-coders`` agent must not be able to
advance ``main`` by raw Git; only the sanctioned source publisher (or an
explicit, recorded override) may.
"""

from __future__ import annotations

import getpass
import json
import os
import pwd
import subprocess
from pathlib import Path

import pytest

from tgw import doctor_cli
from tgw import main_ref_guard as guard

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

try:
    _UNPRIV = pwd.getpwnam("nobody")
except KeyError:  # pragma: no cover - environment without a nobody account
    _UNPRIV = None

#: When the suite itself runs as root, ``root`` is always a sanctioned publisher,
#: so the git-level abort can only be exercised by dropping the git subprocess to
#: an unprivileged uid.  When the suite runs as an ordinary user no drop is
#: needed -- that user is already a non-publisher.
_NEED_DROP = os.getuid() == 0


def _can_drop_to_unpriv() -> bool:
    if not _NEED_DROP or _UNPRIV is None:
        return not _NEED_DROP
    try:
        subprocess.run(
            ["true"],
            check=True,
            user=_UNPRIV.pw_uid,
            group=_UNPRIV.pw_gid,
            extra_groups=[],
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


# Skip the git-level abort tests only if we are root AND cannot actually drop
# privileges (e.g. a single-user namespace).  On a real multi-user root runner
# the drop works and the tests exercise the real reference-transaction abort.
_CANNOT_DROP = _NEED_DROP and not _can_drop_to_unpriv()


def _make_tree_accessible(path: Path) -> None:
    """Let an unprivileged uid read/write the whole repo (root-only test aid)."""
    subprocess.run(["chmod", "-R", "a+rwX", str(path)], check=True)


def _git(
    repo: Path,
    *args: str,
    env: dict | None = None,
    drop_privileges: bool = False,
) -> subprocess.CompletedProcess:
    kwargs: dict = {}
    full_env = {**os.environ, **(env or {})}
    cmd = ["git", "-C", str(repo)]
    if drop_privileges and _NEED_DROP and _UNPRIV is not None:
        _make_tree_accessible(repo)
        kwargs.update(user=_UNPRIV.pw_uid, group=_UNPRIV.pw_gid, extra_groups=[])
        full_env.update(
            HOME=str(repo),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL="/dev/null",
        )
        cmd += ["-c", "safe.directory=*"]
    return subprocess.run(
        [*cmd, *args],
        check=False,
        capture_output=True,
        text=True,
        env=full_env,
        **kwargs,
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


def test_parse_transaction_input_accepts_symref_target() -> None:
    """githooks(5): a symbolic-ref update line uses a ``ref:`` target, not an OID.

    Finding 2: a HEAD symref line bundled with a real ``refs/heads/main`` OID
    update must parse, not raise (which would abort the whole transaction, even
    for the sanctioned publisher).
    """
    text = (
        f"{A} {B} refs/heads/main\n"
        "ref:refs/heads/old ref:refs/heads/main HEAD\n"
    )
    parsed = guard.parse_transaction_input(text)
    assert [u.name for u in parsed] == ["refs/heads/main", "HEAD"]
    # only the real main OID update is a protected change
    assert guard.protected_updates(parsed) == _updates((A, B, "refs/heads/main"))


def test_run_hook_allows_publisher_bundle_with_symref(tmp_path: Path) -> None:
    rc = guard.run_hook(
        ["reference-transaction", "prepared"],
        f"{A} {B} refs/heads/main\nref:refs/heads/x ref:refs/heads/y HEAD\n",
        environ={},
        common_git_dir=tmp_path,
        publisher_identities=(getpass.getuser(),),
    )
    assert rc == 0


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


def test_evaluate_allows_root_but_records_it() -> None:
    # root is no longer an implicit silent publisher: a raw root advance is
    # allowed (receipt-driven recovery) but always leaves a durable record.
    decision = guard.evaluate(
        "prepared",
        _updates((A, B, "refs/heads/main")),
        uid=0,
        caller_name="root",
        publisher_identities=("db",),
    )
    assert decision.allowed
    assert decision.override is not None
    assert decision.override["implicit_root"] is True
    assert decision.override["caller_uid"] == 0


def test_evaluate_root_with_explicit_reason_is_not_implicit() -> None:
    decision = guard.evaluate(
        "prepared",
        _updates((A, B, "refs/heads/main")),
        uid=0,
        caller_name="root",
        publisher_identities=("db",),
        override_value="incident 1942",
    )
    assert decision.allowed
    assert decision.override is not None
    assert decision.override["implicit_root"] is False
    assert decision.override["justification"] == "incident 1942"


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
    a = guard.render_hook_script(source_path="/x/src", common_git_dir="/x/.git")
    b = guard.render_hook_script(source_path="/x/src", common_git_dir="/x/.git")
    assert a == b
    assert f"{guard.SENTINEL} {guard.GUARD_VERSION}" in a
    assert "refs/heads/main" in a
    assert "'/x/.git'" in a  # common git dir embedded literally


def test_hook_prefilter_shortcircuits_without_importing_tgw(tmp_path: Path) -> None:
    script = tmp_path / "reference-transaction"
    script.write_text(
        guard.render_hook_script(
            source_path="/nonexistent/does/not/import", common_git_dir=tmp_path
        )
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
        guard.render_hook_script(
            source_path="/nonexistent/does/not/import", common_git_dir=tmp_path
        )
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


def test_status_detects_body_edit_even_when_config_deleted(repo: Path) -> None:
    """Editing the hook AND deleting guard.json still reads as ``modified``.

    The expected hook body is re-rendered from the installed package, so the
    tamper cannot be self-suppressed by removing the only writable hash anchor.
    """
    guard.install_guard(repo, source_path=SRC)
    hook = guard.hooks_dir(repo) / "reference-transaction"
    hook.write_text(hook.read_text().replace("DO NOT EDIT", "DO NOT EDIT (hah)"))
    (guard.guard_state_dir(repo) / guard.GUARD_CONFIG_NAME).unlink()
    status = guard.guard_status(repo)
    assert status["integrity"] == "modified"
    assert status["active"] is False


def test_status_reports_config_missing_when_only_guard_json_gone(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC)
    (guard.guard_state_dir(repo) / guard.GUARD_CONFIG_NAME).unlink()
    status = guard.guard_status(repo)
    assert status["integrity"] == "config-missing"
    assert status["active"] is False


def test_status_reports_removed_after_hook_deletion(repo: Path) -> None:
    """A silent ``rm`` of an installed hook is tamper-evident (not ``absent``)."""
    guard.install_guard(repo, source_path=SRC)
    (guard.hooks_dir(repo) / "reference-transaction").unlink()
    status = guard.guard_status(repo)
    assert status["integrity"] == "removed"
    result = doctor_cli.check_main_ref_guard(doctor_cli.DoctorPaths(repository=repo))
    assert result["state"] == "FAIL"


def test_status_rejects_hook_with_redirected_source_path(repo: Path) -> None:
    """A byte-valid render that points ``sys.path`` at an attacker dir is ``modified``.

    Finding 2: ``guard_status`` re-derives the expected body from trusted inputs
    only (the installed package's own source path), never from the ``source_path``
    embedded in the group-writable hook, so ``render_hook_script`` with an
    attacker-chosen path cannot round-trip to ``ok``.
    """
    guard.install_guard(repo, source_path=SRC)
    hook = guard.hooks_dir(repo) / "reference-transaction"
    evil = guard.render_hook_script(
        source_path="/tmp/evil",
        common_git_dir=guard.common_git_dir(repo),
        publisher_identities=guard.DEFAULT_PUBLISHER_IDENTITIES,
    )
    hook.write_text(evil)
    hook.chmod(0o755)
    status = guard.guard_status(repo)
    assert status["integrity"] == "modified"
    assert status["active"] is False


def test_status_rejects_hook_with_widened_publisher_list(repo: Path) -> None:
    """Finding 1: the embedded allow-list is anchored to the package constant."""
    guard.install_guard(repo, source_path=SRC)
    hook = guard.hooks_dir(repo) / "reference-transaction"
    widened = guard.render_hook_script(
        source_path=str(guard.PACKAGE_SOURCE_PATH),
        common_git_dir=guard.common_git_dir(repo),
        publisher_identities=("claude", *guard.DEFAULT_PUBLISHER_IDENTITIES),
    )
    hook.write_text(widened)
    hook.chmod(0o755)
    status = guard.guard_status(repo)
    assert status["integrity"] == "modified"
    assert status["active"] is False


def test_guard_json_publisher_list_is_ignored(repo: Path) -> None:
    """Finding 1: rewriting ``guard.json`` grants no authority and no ``ok`` loss."""
    guard.install_guard(repo, source_path=SRC)
    cfg = guard.guard_state_dir(repo) / guard.GUARD_CONFIG_NAME
    data = json.loads(cfg.read_text())
    data["publisher_identities"] = ["claude", "deepseek", "codex"]
    cfg.write_text(json.dumps(data))
    status = guard.guard_status(repo)
    assert status["integrity"] == "ok"
    assert status["publisher_identities"] == list(guard.DEFAULT_PUBLISHER_IDENTITIES)


def test_status_rejects_forged_guard_json_hash(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC)
    cfg = guard.guard_state_dir(repo) / guard.GUARD_CONFIG_NAME
    data = json.loads(cfg.read_text())
    data["hook_sha256"] = "sha256:" + "0" * 64
    cfg.write_text(json.dumps(data))
    status = guard.guard_status(repo)
    assert status["integrity"] == "modified"
    assert status["active"] is False


@pytest.mark.skipif(os.getuid() != 0, reason="implicit-root path only exercisable as root")
def test_root_raw_advance_succeeds_and_is_recorded(repo: Path) -> None:
    """Finding 5: a root-performed raw advance still leaves a durable record."""
    guard.install_guard(repo, source_path=SRC, publisher_identities=("no-such-publisher",))
    before = _head(repo)
    (repo / "b").write_text("more\n")
    _git(repo, "add", ".")
    done = _git(repo, "commit", "-m", "root advance", env={guard.OVERRIDE_ENV: ""})
    assert done.returncode == 0, done.stderr
    assert _head(repo) != before
    common = guard.common_git_dir(repo)
    log = common / guard.GUARD_STATE_DIRNAME / guard.OVERRIDE_LOG_NAME
    lines = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert lines and lines[-1]["implicit_root"] is True
    assert lines[-1]["caller_uid"] == 0


def test_hook_embeds_common_git_dir(repo: Path) -> None:
    result = guard.install_guard(repo, source_path=SRC)
    body = Path(result["hook_path"]).read_text()
    assert repr(str(guard.common_git_dir(repo))) in body


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
# override log: permission posture + tamper evidence (Finding 1)
# --------------------------------------------------------------------------- #
def _record(common: Path, reason: str) -> None:
    guard.record_override_event(
        common,
        {
            "justification": reason,
            "caller_name": "claude",
            "caller_uid": 1000,
            "implicit_root": False,
            "protected_updates": [],
        },
    )


def test_install_pins_state_dir_and_log_permissions(repo: Path) -> None:
    """The audit trail's permission posture is explicit, not left to the umask."""
    guard.install_guard(repo, source_path=SRC)
    state_dir = guard.guard_state_dir(repo)
    log = state_dir / guard.OVERRIDE_LOG_NAME
    dir_mode = state_dir.stat().st_mode
    # group-writable + setgid so a non-publisher emergency override can append
    assert dir_mode & 0o020, oct(dir_mode)
    assert dir_mode & 0o2000, oct(dir_mode)
    assert log.stat().st_mode & 0o020, oct(log.stat().st_mode)


def test_override_log_is_hash_chained(repo: Path) -> None:
    common = guard.common_git_dir(repo)
    _record(common, "one")
    _record(common, "two")
    state, count = guard.verify_override_log(common)
    assert (state, count) == ("ok", 2)
    lines = [
        json.loads(x)
        for x in (common / guard.GUARD_STATE_DIRNAME / guard.OVERRIDE_LOG_NAME)
        .read_text()
        .splitlines()
        if x.strip()
    ]
    assert [e["seq"] for e in lines] == [1, 2]
    assert lines[0]["prev_sha256"] == guard._OVERRIDE_LOG_GENESIS
    assert lines[1]["prev_sha256"] != guard._OVERRIDE_LOG_GENESIS


def test_status_flags_truncated_override_log(repo: Path) -> None:
    """Finding 1: erasing the log is now as detectable as removing the hook."""
    guard.install_guard(repo, source_path=SRC)
    common = guard.common_git_dir(repo)
    _record(common, "incident 1942")
    assert guard.guard_status(repo)["integrity"] == "ok"

    log = common / guard.GUARD_STATE_DIRNAME / guard.OVERRIDE_LOG_NAME
    log.write_text("")  # `: > override-events.log`

    status = guard.guard_status(repo)
    assert status["override_log_tampered"] is True
    assert status["integrity"] == "modified"
    assert status["active"] is False
    result = doctor_cli.check_main_ref_guard(doctor_cli.DoctorPaths(repository=repo))
    assert result["state"] == "FAIL"


def test_status_flags_deleted_override_log(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC)
    common = guard.common_git_dir(repo)
    _record(common, "incident 1942")
    (common / guard.GUARD_STATE_DIRNAME / guard.OVERRIDE_LOG_NAME).unlink()
    status = guard.guard_status(repo)
    assert status["integrity"] == "modified"
    assert status["active"] is False


def test_status_flags_rewritten_override_log_line(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC)
    common = guard.common_git_dir(repo)
    _record(common, "one")
    _record(common, "two")
    log = common / guard.GUARD_STATE_DIRNAME / guard.OVERRIDE_LOG_NAME
    lines = log.read_text().splitlines()
    forged = json.loads(lines[0])
    forged["justification"] = "backdated"
    lines[0] = json.dumps(forged, sort_keys=True)
    log.write_text("\n".join(lines) + "\n")
    assert guard.verify_override_log(common)[0] == "broken"
    assert guard.guard_status(repo)["integrity"] == "modified"


def test_reinstall_carries_override_high_water_mark(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC)
    common = guard.common_git_dir(repo)
    _record(common, "incident 1942")
    guard.uninstall_guard(repo)  # drops guard.json, keeps the log
    guard.install_guard(repo, source_path=SRC)
    cfg = json.loads((guard.guard_state_dir(repo) / guard.GUARD_CONFIG_NAME).read_text())
    assert cfg["override_event_count"] == 1
    # a later truncation is still caught against the carried-forward baseline
    (common / guard.GUARD_STATE_DIRNAME / guard.OVERRIDE_LOG_NAME).write_text("")
    assert guard.guard_status(repo)["integrity"] == "modified"


def test_record_override_event_raises_when_log_unwritable(repo: Path, tmp_path: Path) -> None:
    """A non-writable state dir yields MainRefGuardError, not a bare OSError."""
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    (frozen / guard.GUARD_STATE_DIRNAME).mkdir()
    (frozen / guard.GUARD_STATE_DIRNAME / guard.OVERRIDE_LOG_NAME).touch()
    os.chmod(frozen / guard.GUARD_STATE_DIRNAME / guard.OVERRIDE_LOG_NAME, 0o444)
    os.chmod(frozen / guard.GUARD_STATE_DIRNAME, 0o555)
    try:
        if os.getuid() == 0:
            pytest.skip("root ignores file permission bits")
        with pytest.raises(guard.MainRefGuardError):
            _record(frozen, "cannot land")
    finally:
        os.chmod(frozen / guard.GUARD_STATE_DIRNAME, 0o755)


def test_run_hook_refuses_when_override_cannot_be_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 1 (symmetric half): an unrecordable override is a clean refusal,
    not an unhandled traceback that aborts the transaction."""

    def _boom(*_a, **_k):
        raise guard.MainRefGuardError("state dir is read-only")

    monkeypatch.setattr(guard, "record_override_event", _boom)
    rc = guard.run_hook(
        ["reference-transaction", "prepared"],
        f"{A} {B} refs/heads/main\n",
        environ={guard.OVERRIDE_ENV: "emergency"},
        common_git_dir=tmp_path,
        publisher_identities=("no-such-publisher",),
    )
    assert rc == 1


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


@pytest.mark.skipif(
    _CANNOT_DROP, reason="root suite with no unprivileged account to drop to"
)
def test_ordinary_agent_is_refused(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC, publisher_identities=("no-such-publisher",))
    before = _head(repo)
    (repo / "b").write_text("more\n")
    _git(repo, "add", ".", drop_privileges=True)
    done = _git(
        repo,
        "commit",
        "-m",
        "raw advance",
        env={guard.OVERRIDE_ENV: ""},
        drop_privileges=True,
    )
    assert done.returncode != 0
    assert "tgw main-ref guard: refused" in done.stderr
    assert _head(repo) == before  # ref unchanged


@pytest.mark.skipif(
    _CANNOT_DROP, reason="root suite with no unprivileged account to drop to"
)
def test_non_main_branch_is_unaffected_by_guard(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC, publisher_identities=("no-such-publisher",))
    assert _git(repo, "checkout", "-b", "topic", drop_privileges=True).returncode == 0
    (repo / "b").write_text("more\n")
    _git(repo, "add", ".", drop_privileges=True)
    done = _git(repo, "commit", "-m", "topic work", drop_privileges=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(
    _CANNOT_DROP, reason="root suite with no unprivileged account to drop to"
)
def test_explicit_override_advances_main_and_is_recorded(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC, publisher_identities=("no-such-publisher",))
    before = _head(repo)
    (repo / "b").write_text("more\n")
    _git(repo, "add", ".", drop_privileges=True)
    done = _git(
        repo,
        "commit",
        "-m",
        "override advance",
        env={guard.OVERRIDE_ENV: "incident recovery ticket 1942"},
        drop_privileges=True,
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


def test_doctor_check_warns_after_override_event(repo: Path) -> None:
    """Finding 3: an out-of-band main advance takes the canonical host off green."""
    guard.install_guard(repo, source_path=SRC)
    guard.record_override_event(
        guard.common_git_dir(repo),
        {
            "justification": "incident 1942",
            "caller_name": "claude",
            "caller_uid": 1000,
            "implicit_root": False,
            "protected_updates": [],
        },
    )
    result = doctor_cli.check_main_ref_guard(
        doctor_cli.DoctorPaths(repository=repo)
    )
    assert result["state"] == "WARN"
    assert result["evidence"]["override_event_count"] == 1
    assert result["evidence"]["integrity"] == "ok"


def test_doctor_check_fails_on_tampered_hook(repo: Path) -> None:
    guard.install_guard(repo, source_path=SRC)
    hook = guard.hooks_dir(repo) / "reference-transaction"
    hook.write_text(hook.read_text() + "\n# tampered\n")
    result = doctor_cli.check_main_ref_guard(
        doctor_cli.DoctorPaths(repository=repo)
    )
    assert result["state"] == "FAIL"
