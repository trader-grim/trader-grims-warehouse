from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tgw.workers import codex_implement


@pytest.fixture(autouse=True)
def _isolated_codex_auth(monkeypatch, tmp_path_factory):
    """Runner tests never borrow credentials from the executing Unix user."""
    auth = tmp_path_factory.mktemp("codex-auth") / "auth.json"
    auth.write_text('{"test": true}\n', encoding="utf-8")
    auth.chmod(0o600)
    monkeypatch.setattr(codex_implement, "_codex_auth_path", lambda: auth)


@pytest.fixture(autouse=True)
def _no_inherited_worktree_lease(monkeypatch):
    """Controller runs inherit worker-owned descriptors and archive roots.

    ``codex_implement.run()`` treats a set ``TGW_CODING_WORKTREE_LEASE_FD`` as
    an inherited lease and validates it against the exact worktree inode.  The
    runner tests call ``run()`` against disposable ``tmp_path`` repositories,
    so a leaked descriptor from the dispatching worker makes every
    git-backed test fail with ``inherited worktree lease state for the wrong
    inode``.  Similarly ``TGW_CODING_PRESERVATION_ARCHIVE_ROOT`` points at the
    operator-provisioned host archive, which is not a protected same-filesystem
    ``tgw-coders`` directory inside the controller's private mount namespace;
    ``retire_preservation`` then fails candidate-close tests.  Clear both so
    each test exercises the runner exactly like a standalone invocation.
    """
    monkeypatch.delenv("TGW_CODING_WORKTREE_LEASE_FD", raising=False)
    monkeypatch.delenv("TGW_CODING_PRESERVATION_ARCHIVE_ROOT", raising=False)


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def _job(**overrides):
    value = {
        "todo_id": 1745,
        "treatment_id": "codex-implement",
        "treatment_version": "1",
        "task_spec": {"schema": "coding-task/v1", "todo_id": 1745, "agent": "codex", "body": "implement the bounded feature"},
    }
    value.update(overrides)
    return value


def _invoke(report, edit=None, returncode=0):
    def invoke(command, *, cwd, **_kwargs):
        if edit:
            edit(Path(cwd))
        output = Path(command[command.index("-o") + 1])
        output.write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(command, returncode, "", "failure" if returncode else "")

    return invoke


def test_satisfied_closes_exact_source_commit(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")
    result = codex_implement.run(
        _job(),
        repo,
        invoke=_invoke(
            {"status": "implemented", "summary": "added feature", "tests": ["focused tests passed"]},
            edit=lambda path: (path / "feature.py").write_text("VALUE = 1\n", encoding="utf-8"),
        ),
    )
    assert result["outcome"] == "satisfied"
    assert result["established_conditions"] == ["implemented"]
    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    assert candidate != baseline
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True,
        text=True, capture_output=True,
    ).stdout == ""
    closed = result["artifacts"][-1]
    assert closed["kind"] == "closed_candidate"
    assert closed["commit"] == candidate
    assert len(closed["tree"]) == 40
    assert closed["base_commit"] == baseline
    assert closed["changed_paths"] == ["feature.py"]
    diff = next(item for item in result["artifacts"] if item["kind"] == "git_diff")
    assert diff["changed_paths"] == closed["changed_paths"]
    assert "feature.py" in diff["detail"]


def test_runner_uses_ordinary_unix_boundary_without_approval_gate(
    tmp_path,
    monkeypatch,
):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")
    captured = []

    def invoke(command, *, cwd, env, **_kwargs):
        captured.extend(command)
        ephemeral_home = Path(env["CODEX_HOME"])
        assert ephemeral_home.parent.name.startswith(".tgw-codex-implement-")
        assert ephemeral_home.parent.parent == repo
        assert (ephemeral_home / "auth.json").is_file()
        assert (ephemeral_home / "auth.json").stat().st_mode & 0o777 == 0o600
        config = ephemeral_home / "config.toml"
        assert config.stat().st_mode & 0o777 == 0o600
        expected = (
            "[mcp_servers.tgw-context]\n"
            'command = "/opt/TGW/tgw-lib/bin/tgw-context-mcp"\n'
            "args = []\n"
        )
        expected += "".join(
            f"\n[mcp_servers.tgw-context.tools.{tool}]\n"
            'approval_mode = "approve"\n'
            for tool in codex_implement._CONTEXT_TOOLS
        )
        assert config.read_text(encoding="utf-8") == expected
        Path(command[command.index("-o") + 1]).write_text(
            json.dumps({"status": "blocked", "summary": "bounded", "tests": []}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    codex_implement.run(_job(), repo, invoke=invoke)

    assert "--approve-for-me" not in captured
    assert captured[captured.index("--ask-for-approval") + 1] == "never"
    assert captured[captured.index("--sandbox") + 1] == "danger-full-access"
    assert "workspace-write" not in captured
    assert captured[captured.index("-C") + 1] == str(repo)
    assert not {
        "sudo", "ssh", "tgw-prod", "remote-provision", "approval-card",
        "execution-card", "actor-fleet",
    }.intersection(captured)
    assert "--ignore-user-config" not in captured


def test_actual_runner_boundary_retains_unix_identity_and_local_transports(
    tmp_path, tmp_path_factory, monkeypatch,
):
    repo = _repo(tmp_path)
    helper = tmp_path_factory.mktemp("codex-boundary") / "codex"
    helper.write_text(
        """#!{python}
import json, os, socket, sys
args = sys.argv[1:]
assert args[args.index('--sandbox') + 1] == 'danger-full-access'
assert args[args.index('--ask-for-approval') + 1] == 'never'
assert os.getcwd() == args[args.index('-C') + 1]
assert os.environ['CODEX_HOME'].startswith(os.getcwd() + '/.tgw-codex-implement-')
unix_path = {socket_path!r}
with socket.socket(socket.AF_UNIX) as server:
    server.bind(unix_path); server.listen()
    with socket.socket(socket.AF_UNIX) as client:
        client.connect(unix_path)
        connection, _ = server.accept(); connection.close()
with socket.socket() as server:
    server.bind(('127.0.0.1', 0)); server.listen()
    port = server.getsockname()[1]
    with socket.socket() as client:
        client.connect(('127.0.0.1', port))
        connection, _ = server.accept(); connection.close()
json.dump({{'status': 'blocked', 'summary': 'identity=' + str(os.getuid()), 'tests': ['unix socket', 'loopback']}}, open(args[args.index('-o') + 1], 'w'))
""".format(python=sys.executable, socket_path=str(helper.parent / "transport.sock")),
        encoding="utf-8",
    )
    helper.chmod(0o700)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: str(helper))

    result = codex_implement.run(_job(), repo)

    assert result["outcome"] == "partial", result
    assert f"identity={os.getuid()}" in result["artifacts"][0]["detail"]


def test_runner_fails_closed_when_local_context_mcp_is_missing(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_CONTEXT_MCP", tmp_path / "missing-context-mcp")
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    try:
        codex_implement.run(
            _job(),
            repo,
            invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("called")),
        )
    except Exception as exc:
        assert "tgw-context MCP is unavailable" in str(exc)
    else:
        raise AssertionError("runner accepted a missing context MCP")


def test_model_success_without_diff_is_partial(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")
    result = codex_implement.run(
        _job(),
        repo,
        invoke=_invoke({"status": "implemented", "summary": "nothing changed", "tests": []}),
    )
    assert result["outcome"] == "partial"
    assert result["established_conditions"] == []


def test_runner_refuses_preexisting_mutable_source(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "operator-work.py").write_text("preserve = True\n", encoding="utf-8")
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    try:
        codex_implement.run(
            _job(),
            repo,
            invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("called")
            ),
        )
    except Exception as exc:
        assert "source-clean worktree" in str(exc)
    else:
        raise AssertionError("runner accepted preexisting mutable source")


def test_runner_rejects_another_actor_before_codex(tmp_path):
    repo = _repo(tmp_path)
    job = _job(task_spec={"schema": "coding-task/v1", "todo_id": 1745, "agent": "claude", "body": "task"})
    try:
        codex_implement.run(job, repo, invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("called")))
    except Exception as exc:
        assert "task specification" in str(exc)
    else:
        raise AssertionError("wrong actor was accepted")


def test_runner_detects_model_commit_as_conflict(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    def commit(path: Path):
        (path / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "feature.py"], cwd=path, check=True)
        subprocess.run(["git", "commit", "-m", "forbidden"], cwd=path, check=True, capture_output=True)

    result = codex_implement.run(
        _job(),
        repo,
        invoke=_invoke({"status": "implemented", "summary": "committed", "tests": []}, edit=commit),
    )
    assert result["outcome"] == "conflict"
    assert result["established_conditions"] == []


def test_commit_failure_recovers_index_and_preserves_source(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")
    real_git = codex_implement._git

    def fail_commit(cwd, *args):
        if "commit" in args:
            raise codex_implement.HardFailure("simulated commit failure")
        return real_git(cwd, *args)

    monkeypatch.setattr(codex_implement, "_git", fail_commit)
    with pytest.raises(codex_implement.HardFailure, match="simulated commit failure"):
        codex_implement.run(
            _job(),
            repo,
            invoke=_invoke(
                {"status": "implemented", "summary": "added feature", "tests": []},
                edit=lambda path: (path / "feature.py").write_text("VALUE = 1\n"),
            ),
        )
    assert subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=repo, check=False
    ).returncode == 0
    assert (repo / "feature.py").read_text() == "VALUE = 1\n"


def test_runner_refuses_ignored_mutable_files(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text("cache/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore cache"], cwd=repo, check=True, capture_output=True)
    (repo / "cache").mkdir()
    (repo / "cache/value").write_text("mutable\n")
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    with pytest.raises(codex_implement.HardFailure, match="source-clean worktree"):
        codex_implement.run(
            _job(),
            repo,
            invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("called")),
        )


def test_runner_removes_only_generated_caches_before_and_after_model(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text(
        "__pycache__/\n.pytest_cache/\n.ruff_cache/\nprivate-cache/\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "ignore generated caches"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / ".ruff_cache").mkdir()
    (repo / ".ruff_cache/state").write_text("generated\n", encoding="utf-8")
    pycache = repo / "src/package/__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "module.cpython-313.pyc").write_bytes(b"generated")
    nested = repo / "tests/__pycache__"
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    def edit(path: Path) -> None:
        (path / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        (path / ".pytest_cache").mkdir()
        (path / ".pytest_cache/state").write_text("generated\n", encoding="utf-8")
        nested.mkdir(parents=True)
        (nested / "test_feature.cpython-313.pyc").write_bytes(b"generated")

    result = codex_implement.run(
        _job(),
        repo,
        invoke=_invoke(
            {
                "status": "implemented",
                "summary": "added feature",
                "tests": ["focused tests passed"],
            },
            edit=edit,
        ),
    )

    assert result["outcome"] == "satisfied"
    cleanup = next(
        artifact
        for artifact in result["artifacts"]
        if artifact["kind"] == "transient_cache_cleanup"
    )
    assert cleanup["paths"] == [
        ".pytest_cache",
        ".ruff_cache",
        "src/package/__pycache__",
        "tests/__pycache__",
    ]
    assert not (repo / ".ruff_cache").exists()
    assert not (repo / ".pytest_cache").exists()
    assert not pycache.exists()
    assert not nested.exists()


def test_runner_still_refuses_noncache_ignored_work_with_generated_caches(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text(
        ".ruff_cache/\nprivate-cache/\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "ignore cache classes"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / ".ruff_cache").mkdir()
    (repo / ".ruff_cache/state").write_text("generated\n", encoding="utf-8")
    (repo / "private-cache").mkdir()
    (repo / "private-cache/unique").write_text("preserve\n", encoding="utf-8")
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    with pytest.raises(codex_implement.HardFailure, match="source-clean worktree"):
        codex_implement.run(
            _job(),
            repo,
            invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("called")
            ),
        )

    assert not (repo / ".ruff_cache").exists()
    assert (repo / "private-cache/unique").read_text(encoding="utf-8") == "preserve\n"


def test_runner_removes_generated_cache_after_model_failure(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text(".pytest_cache/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "ignore pytest cache"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    def create_cache(path: Path) -> None:
        (path / ".pytest_cache").mkdir()
        (path / ".pytest_cache/state").write_text("generated\n", encoding="utf-8")

    result = codex_implement.run(
        _job(),
        repo,
        invoke=_invoke(
            {"status": "blocked", "summary": "failed", "tests": []},
            edit=create_cache,
            returncode=1,
        ),
    )

    assert result["outcome"] == "failed"
    assert not (repo / ".pytest_cache").exists()
    assert result["artifacts"][-1]["kind"] == "transient_cache_cleanup"


def test_runner_removes_generated_cache_when_model_invocation_raises(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text(".ruff_cache/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "ignore ruff cache"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    def raise_after_cache(_command, *, cwd, **_kwargs):
        path = Path(cwd) / ".ruff_cache"
        path.mkdir()
        (path / "state").write_text("generated\n", encoding="utf-8")
        raise OSError("model invocation failed")

    with pytest.raises(OSError, match="model invocation failed"):
        codex_implement.run(_job(), repo, invoke=raise_after_cache)

    assert not (repo / ".ruff_cache").exists()


def test_runner_refuses_a_concurrent_worktree_lease(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    with codex_implement._exclusive_worktree_lease(repo):
        with pytest.raises(codex_implement.HardFailure, match="already leased"):
            codex_implement.run(
                _job(),
                repo,
                invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("called")),
            )
    assert not (repo / ".git/tgw-coding.lock").exists()


def test_runner_uses_exact_inherited_worker_lease(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    with codex_implement._exclusive_worktree_lease(repo) as descriptor:
        monkeypatch.setenv("TGW_CODING_WORKTREE_LEASE_FD", str(descriptor))
        result = codex_implement.run(
            _job(),
            repo,
            invoke=_invoke(
                {"status": "blocked", "summary": "still bounded", "tests": []}
            ),
        )

    assert result["outcome"] == "partial"


def test_worktree_lease_accepts_a_normal_repository_common_dir(tmp_path):
    repo = _repo(tmp_path)

    with codex_implement._exclusive_worktree_lease(repo):
        assert (repo / ".git").is_dir()


def test_candidate_close_disables_hooks_and_signing(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")
    observed = []
    real_git = codex_implement._git

    def inspect_git(cwd, *args):
        if "commit" in args:
            observed.extend(args)
        return real_git(cwd, *args)

    monkeypatch.setattr(codex_implement, "_git", inspect_git)
    result = codex_implement.run(
        _job(),
        repo,
        invoke=_invoke(
            {"status": "implemented", "summary": "added feature", "tests": []},
            edit=lambda path: (path / "feature.py").write_text("VALUE = 1\n"),
        ),
    )

    assert result["outcome"] == "satisfied"
    assert "--no-verify" in observed
    assert "commit.gpgSign=false" in observed
    assert "core.hooksPath=/dev/null" in observed


def test_late_source_is_preserved_outside_clean_candidate(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")
    real_git = codex_implement._git
    wrote_late_source = False

    def late_write(cwd, *args):
        nonlocal wrote_late_source
        result = real_git(cwd, *args)
        if "commit" in args and not wrote_late_source:
            (cwd / "late.py").write_text("outside_lease = True\n")
            wrote_late_source = True
        return result

    monkeypatch.setattr(codex_implement, "_git", late_write)
    result = codex_implement.run(
        _job(),
        repo,
        invoke=_invoke(
            {"status": "implemented", "summary": "added feature", "tests": []},
            edit=lambda path: (path / "feature.py").write_text("VALUE = 1\n"),
        ),
    )

    assert result["outcome"] == "satisfied"
    recovery = result["artifacts"][-1]
    assert recovery["kind"] == "late_source_recovery"
    assert len(recovery["stash"]) == 40
    assert not (repo / "late.py").exists()
    assert subprocess.run(
        ["git", "status", "--porcelain", "--ignored=matching"],
        cwd=repo, check=True, text=True, capture_output=True,
    ).stdout == ""
    assert "late.py" in subprocess.run(
        ["git", "stash", "show", "--include-untracked", "--name-only"],
        cwd=repo, check=True, text=True, capture_output=True,
    ).stdout


def test_next_generation_recovers_dirty_closed_candidate_without_model(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    (repo / "feature.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "already closed"],
        cwd=repo, check=True, capture_output=True,
    )
    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    (repo / "late.py").write_text("preserve = True\n")
    (repo / "implementation-receipt.json").write_text("stale evidence\n")
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    result = codex_implement.run(
        _job(plan_binding={"source_commit": baseline}),
        repo,
        invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("model reran")
        ),
    )

    assert result["outcome"] == "satisfied"
    assert result["artifacts"][0]["commit"] == candidate
    assert result["artifacts"][-1]["kind"] == "late_source_recovery"
    assert (repo / "implementation-receipt.json").read_text() == "stale evidence\n"
    assert not (repo / "late.py").exists()


def test_prompt_forbids_deploy_commit_config_secrets_and_satellites():
    prompt = codex_implement._prompt(_job()["task_spec"])
    for word in ("commit", "deploy", "configuration", "secrets", "satellite"):
        assert word in prompt
    assert "CLAUDE.md does not govern Codex" in prompt


def test_manual_executor_waits_writes_card_and_closes_candidate(tmp_path, monkeypatch):
    import threading
    import time as _time
    repo = _repo(tmp_path)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "manual")
    monkeypatch.setattr(codex_implement, "_manual_poll_seconds", lambda: 0.01)
    monkeypatch.setattr(codex_implement, "_manual_timeout_seconds", lambda: 10)
    holder: dict[str, object] = {}

    def worker() -> None:
        holder["result"] = codex_implement.run(_job(), repo)

    thread = threading.Thread(target=worker)
    thread.start()
    card = repo / ".tgw-coding-history/implementation/manual/task.json"
    deadline = _time.monotonic() + 5
    while not card.is_file() and _time.monotonic() < deadline:
        _time.sleep(0.01)
    assert card.is_file()
    payload = json.loads(card.read_text(encoding="utf-8"))
    assert payload["schema"] == "tgw-manual-implementation-task/v1"
    assert payload["todo_id"] == 1745
    assert payload["body"] == "implement the bounded feature"
    (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (card.parent / "done.json").write_text(
        json.dumps({"status": "implemented", "summary": "manual done", "tests": ["focused"]}),
        encoding="utf-8",
    )
    thread.join(10)
    assert not thread.is_alive()
    result = holder["result"]
    assert result["outcome"] == "satisfied"
    assert result["established_conditions"] == ["implemented"]
    assert any(item["kind"] == "manual_summary" for item in result["artifacts"])
    assert any(item["kind"] == "tests_reported" for item in result["artifacts"])
    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    assert candidate != baseline
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True,
        text=True, capture_output=True,
    ).stdout == ""
    closed = next(item for item in result["artifacts"] if item["kind"] == "closed_candidate")
    assert closed["commit"] == candidate
    assert closed["base_commit"] == baseline
    assert closed["changed_paths"] == ["feature.py"]


def test_manual_executor_invalid_done_marker_fails(tmp_path, monkeypatch):
    import threading
    import time as _time
    repo = _repo(tmp_path)
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "manual")
    monkeypatch.setattr(codex_implement, "_manual_poll_seconds", lambda: 0.01)
    monkeypatch.setattr(codex_implement, "_manual_timeout_seconds", lambda: 5)
    holder: dict[str, object] = {}

    def worker() -> None:
        holder["result"] = codex_implement.run(_job(), repo)

    thread = threading.Thread(target=worker)
    thread.start()
    card = repo / ".tgw-coding-history/implementation/manual/task.json"
    deadline = _time.monotonic() + 5
    while not card.is_file() and _time.monotonic() < deadline:
        _time.sleep(0.01)
    assert card.is_file()
    (card.parent / "done.json").write_text("not json", encoding="utf-8")
    thread.join(10)
    assert not thread.is_alive()
    result = holder["result"]
    assert result["outcome"] == "failed"
    assert any(item["kind"] == "manual_failure" for item in result["artifacts"])


def test_manual_executor_timeout_stashes_late_source(tmp_path, monkeypatch):
    import threading
    import time as _time
    repo = _repo(tmp_path)
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "manual")
    monkeypatch.setattr(codex_implement, "_manual_poll_seconds", lambda: 0.01)
    monkeypatch.setattr(codex_implement, "_manual_timeout_seconds", lambda: 0.5)
    holder: dict[str, object] = {}

    def worker() -> None:
        holder["result"] = codex_implement.run(_job(), repo)

    thread = threading.Thread(target=worker)
    thread.start()
    card = repo / ".tgw-coding-history/implementation/manual/task.json"
    deadline = _time.monotonic() + 5
    while not card.is_file() and _time.monotonic() < deadline:
        _time.sleep(0.01)
    assert card.is_file()
    (repo / "wip.py").write_text("WIP = 1\n", encoding="utf-8")
    thread.join(10)
    assert not thread.is_alive()
    result = holder["result"]
    assert result["outcome"] == "failed"
    kinds = [item["kind"] for item in result["artifacts"]]
    assert "manual_timeout" in kinds
    assert "late_source_recovery" in kinds
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True,
        text=True, capture_output=True,
    ).stdout == ""


def test_unknown_executor_fails_closed(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "bogus")
    with pytest.raises(codex_implement.HardFailure, match="unsupported implementation executor"):
        codex_implement.run(_job(), repo)


# ---------------------------------------------------------------------------
# Claude implementation executor (todo #1935)
# ---------------------------------------------------------------------------

def _claude_invoke(report=None, edit=None, returncode=0, stdout=None):
    """Mock subprocess for the claude -p --output-format json invocation."""
    def invoke(command, *, cwd, **_kwargs):
        if edit:
            edit(Path(cwd))
        if stdout is not None:
            out = stdout
        elif report is not None:
            # Claude -p --output-format json emits JSONL; the final result
            # text carries the report object as a JSON string.
            out = json.dumps({"type": "result", "result": json.dumps(report)}) + "\n"
        else:
            out = ""
        return subprocess.CompletedProcess(
            command, returncode, out,
            "claude failure" if returncode else "",
        )
    return invoke


def test_claude_binary_unavailable_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("TGW_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(codex_implement.shutil, "which", lambda name: None)
    with pytest.raises(codex_implement.HardFailure, match="Claude Code executable is unavailable"):
        codex_implement._claude_binary()


def test_claude_binary_uses_configured_path(tmp_path, monkeypatch):
    executable = tmp_path / "claude"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("TGW_CLAUDE_BIN", str(executable))
    assert codex_implement._claude_binary() == str(executable)


def test_claude_report_extracts_trailing_json():
    report = {"status": "implemented", "summary": "claude done", "tests": ["focused"]}
    stdout = (
        '{"type":"system","subtype":"init"}\n'
        + json.dumps({"type": "result", "result": json.dumps(report)})
        + "\n"
    )
    assert codex_implement._claude_report(stdout) == report


def test_claude_report_accepts_plain_text_result():
    report = {"status": "implemented", "summary": "plain", "tests": ["focused"]}
    stdout = '{"type":"result","text":"' + json.dumps(report).replace('"', '\\"') + '"}\n'
    assert codex_implement._claude_report(stdout) == report


def test_claude_report_returns_none_on_garbage():
    assert codex_implement._claude_report("not json at all\n") is None
    assert codex_implement._claude_report("") is None


def test_claude_executor_satisfied_closes_candidate(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "claude")
    monkeypatch.setattr(codex_implement, "_claude_binary", lambda: "/bin/true")
    report = {"status": "implemented", "summary": "claude implemented", "tests": ["focused"]}

    def edit(cwd: Path) -> None:
        (cwd / "feature.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = codex_implement.run(
        _job(),
        repo,
        invoke=_claude_invoke(report=report, edit=edit),
    )
    assert result["outcome"] == "satisfied"
    assert result["established_conditions"] == ["implemented"]
    kinds = [item["kind"] for item in result["artifacts"]]
    assert "claude_summary" in kinds
    assert "tests_reported" in kinds
    assert "closed_candidate" in kinds
    closed = next(item for item in result["artifacts"] if item["kind"] == "closed_candidate")
    assert closed["base_commit"] == baseline
    assert closed["changed_paths"] == ["feature.py"]
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True,
        text=True, capture_output=True,
    ).stdout == ""


def test_claude_executor_nonzero_exit_fails(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "claude")
    monkeypatch.setattr(codex_implement, "_claude_binary", lambda: "/bin/true")
    result = codex_implement.run(
        _job(),
        repo,
        invoke=_claude_invoke(returncode=2),
    )
    assert result["outcome"] == "failed"
    kinds = [item["kind"] for item in result["artifacts"]]
    assert "claude_failure" in kinds


def test_claude_executor_unparseable_report_fails(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "claude")
    monkeypatch.setattr(codex_implement, "_claude_binary", lambda: "/bin/true")
    result = codex_implement.run(
        _job(),
        repo,
        invoke=_claude_invoke(stdout="garbage output without a report\n"),
    )
    assert result["outcome"] == "failed"
    kinds = [item["kind"] for item in result["artifacts"]]
    assert "claude_failure" in kinds
    assert any("not parseable" in item.get("detail", "") for item in result["artifacts"])


def test_claude_executor_invalid_report_contract_fails(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "claude")
    monkeypatch.setattr(codex_implement, "_claude_binary", lambda: "/bin/true")
    result = codex_implement.run(
        _job(),
        repo,
        invoke=_claude_invoke(report={"status": "implemented", "summary": "", "tests": []}),
    )
    assert result["outcome"] == "failed"
    kinds = [item["kind"] for item in result["artifacts"]]
    assert "claude_failure" in kinds
