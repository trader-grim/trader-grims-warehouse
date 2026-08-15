"""PP-MULTIMODEL-001 — tests for TGW Aider MCP server.

All tests monkeypatch subprocess.run and filesystem I/O so no real Aider,
git, or secrets access occurs.
"""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import tgw.aider_mcp_server as ams

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO = ams._REPO_ROOT


def _fake_proc(returncode: int = 0, stdout: str = '', stderr: str = '') -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def _patch_worktree(monkeypatch, tmp_path):
    """Most aider_run_task tests exercise the aider-invocation path, not
    worktree creation or preflight context gathering — patch both to keep
    those tests fast/offline and focused."""
    monkeypatch.setattr(ams, '_ensure_worktree', lambda slug: (tmp_path, None))
    monkeypatch.setattr(ams, '_build_preflight_context', lambda work_dir: '## preflight stub\n')


# ---------------------------------------------------------------------------
# _resolve_files
# ---------------------------------------------------------------------------


def test_resolve_files_valid():
    paths, err = ams._resolve_files(['src/tgw/items.py'])
    assert err is None
    assert len(paths) == 1
    assert paths[0] == (_REPO / 'src/tgw/items.py').resolve()


def test_resolve_files_traversal_rejected():
    _, err = ams._resolve_files(['../../etc/passwd'])
    assert err is not None
    assert 'outside repo' in err


def test_resolve_files_absolute_outside_rejected():
    _, err = ams._resolve_files(['/etc/passwd'])
    assert err is not None
    assert 'outside repo' in err


def test_resolve_files_empty():
    paths, err = ams._resolve_files([])
    assert err is None
    assert paths == []


# ---------------------------------------------------------------------------
# aider_run_task
# ---------------------------------------------------------------------------


def test_run_task_invalid_mode():
    result = json.loads(ams.aider_run_task('do something', ['src/tgw/items.py'], mode='bad'))
    assert result['ok'] is False
    assert 'invalid mode' in result['error']


# ---------------------------------------------------------------------------
# task_slug required (todo #1458) — no shared-checkout fallthrough
# ---------------------------------------------------------------------------


def test_run_task_missing_task_slug_rejected():
    """Omitting task_slug entirely must be rejected, not silently fall
    through to the shared checkout."""
    result = json.loads(ams.aider_run_task('do something', ['src/tgw/items.py']))
    assert result['ok'] is False
    assert 'task_slug is required' in result['error']


def test_run_task_empty_string_task_slug_rejected():
    """The exact reported bug: task_slug='' must be rejected the same as
    an omitted task_slug, never treated as 'use the shared checkout'."""
    result = json.loads(
        ams.aider_run_task('do something', ['src/tgw/items.py'], task_slug='')
    )
    assert result['ok'] is False
    assert 'task_slug is required' in result['error']


def test_run_task_whitespace_task_slug_rejected():
    result = json.loads(
        ams.aider_run_task('do something', ['src/tgw/items.py'], task_slug='   ')
    )
    assert result['ok'] is False
    assert 'task_slug is required' in result['error']


def test_run_task_invalid_task_slug_syntax_rejected():
    result = json.loads(
        ams.aider_run_task('do something', ['src/tgw/items.py'], task_slug='not valid!')
    )
    assert result['ok'] is False
    assert 'invalid task_slug' in result['error']


def test_run_task_empty_files():
    result = json.loads(
        ams.aider_run_task('do something', [], task_slug='1458-test')
    )
    assert result['ok'] is False
    assert 'empty' in result['error']


def test_run_task_traversal_rejected():
    result = json.loads(
        ams.aider_run_task('do something', ['../../bad.py'], task_slug='1458-test')
    )
    assert result['ok'] is False
    assert 'outside repo' in result['error']


def test_run_task_success(tmp_path, monkeypatch):
    audit_log = tmp_path / 'usage.csv'
    monkeypatch.setattr(ams, '_AUDIT_LOG', audit_log)
    monkeypatch.setattr(ams, '_API_KEYS', {'ANTHROPIC_API_KEY': 'sk-test'})
    _patch_worktree(monkeypatch, tmp_path)

    aider_proc = _fake_proc(stdout='Applied changes.\n')
    diff_proc = _fake_proc(stdout='--- a/items.py\n+++ b/items.py\n@@ ... @@\n+guard\n')

    call_count = [0]

    def fake_run(cmd, **kwargs):
        call_count[0] += 1
        if 'git' in cmd:
            return diff_proc
        return aider_proc

    monkeypatch.setattr(subprocess, 'run', fake_run)

    result = json.loads(
        ams.aider_run_task('Add qty guard', ['src/tgw/items.py'], task_slug='1458-test')
    )
    assert result['ok'] is True
    assert result['exit_code'] == 0
    assert '+guard' in result['diff']
    assert 'Applied changes' in result['output']
    assert result['duration_s'] >= 0

    # audit log written
    assert audit_log.exists()
    with audit_log.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]['mode'] == 'edit'
    assert 'items.py' in rows[0]['files']
    assert rows[0]['exit_code'] == '0'


def test_run_task_architect_mode(tmp_path, monkeypatch):
    audit_log = tmp_path / 'usage.csv'
    monkeypatch.setattr(ams, '_AUDIT_LOG', audit_log)
    monkeypatch.setattr(ams, '_API_KEYS', {})
    _patch_worktree(monkeypatch, tmp_path)

    captured_cmd = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        if 'git' in cmd:
            return _fake_proc()
        return _fake_proc(stdout='done')

    monkeypatch.setattr(subprocess, 'run', fake_run)

    result = json.loads(
        ams.aider_run_task(
            'Refactor X', ['src/tgw/api.py'], mode='architect', task_slug='1458-test'
        )
    )
    assert result['ok'] is True
    assert '--architect' in captured_cmd


def test_run_task_no_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(ams, '_AUDIT_LOG', tmp_path / 'usage.csv')
    monkeypatch.setattr(ams, '_API_KEYS', {})
    _patch_worktree(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        if 'git' in cmd:
            return _fake_proc(stdout='')  # no diff
        return _fake_proc(stdout='No changes needed.')

    monkeypatch.setattr(subprocess, 'run', fake_run)

    result = json.loads(
        ams.aider_run_task('Make it better', ['src/tgw/items.py'], task_slug='1458-test')
    )
    assert result['diff'] == '(no changes)'


def test_run_task_aider_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(ams, '_AUDIT_LOG', tmp_path / 'usage.csv')
    monkeypatch.setattr(ams, '_API_KEYS', {})
    _patch_worktree(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        if 'git' in cmd:
            return _fake_proc()
        return _fake_proc(returncode=1, stderr='API error\n')

    monkeypatch.setattr(subprocess, 'run', fake_run)

    result = json.loads(
        ams.aider_run_task('Break things', ['src/tgw/items.py'], task_slug='1458-test')
    )
    assert result['ok'] is False
    assert result['exit_code'] == 1
    assert 'API error' in result['output']


def test_run_task_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(ams, '_AUDIT_LOG', tmp_path / 'usage.csv')
    monkeypatch.setattr(ams, '_API_KEYS', {})
    _patch_worktree(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, ams._TASK_TIMEOUT)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    result = json.loads(
        ams.aider_run_task('Something slow', ['src/tgw/items.py'], task_slug='1458-test')
    )
    assert result['ok'] is False
    assert 'timed out' in result['error']


def test_run_task_output_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(ams, '_AUDIT_LOG', tmp_path / 'usage.csv')
    monkeypatch.setattr(ams, '_API_KEYS', {})
    _patch_worktree(monkeypatch, tmp_path)

    big_output = 'x' * 10000
    big_diff = 'y' * 20000

    def fake_run(cmd, **kwargs):
        if 'git' in cmd:
            return _fake_proc(stdout=big_diff)
        return _fake_proc(stdout=big_output)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    result = json.loads(
        ams.aider_run_task('Something big', ['src/tgw/items.py'], task_slug='1458-test')
    )
    assert len(result['output']) <= 4000
    assert len(result['diff']) <= 8000


# ---------------------------------------------------------------------------
# preflight context injection (todo #1458)
# ---------------------------------------------------------------------------


def test_run_task_injects_preflight_context(tmp_path, monkeypatch):
    """aider_run_task must pass Plan Vault preflight context to Aider via
    the message file — proves the preflight seam is actually wired in, not
    just present as a standalone function."""
    monkeypatch.setattr(ams, '_AUDIT_LOG', tmp_path / 'usage.csv')
    monkeypatch.setattr(ams, '_API_KEYS', {})
    monkeypatch.setattr(ams, '_ensure_worktree', lambda slug: (tmp_path, None))
    monkeypatch.setattr(
        ams, '_build_preflight_context', lambda work_dir: '## preflight stub: 3 inbox files\n'
    )

    captured_msg_contents = []

    def fake_run(cmd, **kwargs):
        if 'git' in cmd:
            return _fake_proc()
        if '--message-file' in cmd:
            msg_path = cmd[cmd.index('--message-file') + 1]
            # aider_run_task deletes the temp message file in its `finally`
            # block, so read it here while the subprocess call is "live".
            with open(msg_path) as f:
                captured_msg_contents.append(f.read())
        return _fake_proc(stdout='done')

    monkeypatch.setattr(subprocess, 'run', fake_run)

    result = json.loads(
        ams.aider_run_task('Add a guard', ['src/tgw/items.py'], task_slug='1458-test')
    )
    assert result['ok'] is True
    assert len(captured_msg_contents) == 1
    written = captured_msg_contents[0]
    assert 'preflight stub: 3 inbox files' in written
    assert 'Add a guard' in written


def test_build_preflight_context_real(tmp_path, monkeypatch):
    """Exercise the real (unpatched) preflight builder against a fake
    work_dir — proves it degrades gracefully rather than raising when the
    expected inbox dir / tgw binary aren't in the tmp tree."""
    monkeypatch.setattr(
        subprocess, 'run',
        lambda cmd, **kw: (_ for _ in ()).throw(FileNotFoundError('tgw not found')),
    )
    ctx = ams._build_preflight_context(tmp_path)
    assert 'Plan Vault preflight' in ctx
    assert 'inbox/claude' in ctx
    assert 'tgw plan check' in ctx


def test_build_preflight_uses_configured_plan_git(tmp_path, monkeypatch):
    observed = {}

    monkeypatch.setattr(
        ams,
        '_plan_runtime_binding',
        lambda: (Path('/opt/TGW/library/plans'), '/run/current-system/sw/bin/git'),
    )

    def fake_live_plan_graph(root, task, **kwargs):
        observed.update(root=root, task=task, **kwargs)
        return {
            'plan_commit': 'fb9fee3',
            'source_envelope': 'source',
            'receiver_profile': 'aider',
            'canonical_authority': '/opt/TGW/library/plans',
        }

    monkeypatch.setattr('tgw.plan_graph.live_plan_graph', fake_live_plan_graph)
    monkeypatch.setattr(subprocess, 'run', lambda cmd, **kw: _fake_proc())

    context = ams._build_preflight_context(tmp_path)

    assert observed['git_path'] == '/run/current-system/sw/bin/git'
    assert observed['root'] == Path('/opt/TGW/library/plans')
    assert 'Standalone Plan commit: fb9fee3' in context


def test_plan_runtime_binding_defaults_to_standalone_root(tmp_path, monkeypatch):
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps({'secrets_root': str(tmp_path / 'secrets')}))
    monkeypatch.setenv('TGW_CONFIG', str(config_path))
    monkeypatch.delenv('TGW_STANDALONE_PLAN_VAULT', raising=False)
    monkeypatch.delenv('TGW_STANDALONE_PLAN_GIT', raising=False)

    root, git_path = ams._plan_runtime_binding()

    assert root == Path('/opt/TGW/library/plans')
    assert git_path == 'git'


def test_plan_runtime_binding_honors_explicit_config(tmp_path, monkeypatch):
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps({
        'secrets_root': str(tmp_path / 'secrets'),
        'standalone_plan_root': '~/plans',
        'plan_git_path': '/run/current-system/sw/bin/git',
    }))
    monkeypatch.setenv('TGW_CONFIG', str(config_path))
    monkeypatch.delenv('TGW_STANDALONE_PLAN_VAULT', raising=False)
    monkeypatch.delenv('TGW_STANDALONE_PLAN_GIT', raising=False)

    root, git_path = ams._plan_runtime_binding()

    assert root == Path('~/plans').expanduser()
    assert git_path == '/run/current-system/sw/bin/git'


# ---------------------------------------------------------------------------
# aider_get_log
# ---------------------------------------------------------------------------


def test_get_log_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ams, '_AUDIT_LOG', tmp_path / 'nonexistent.csv')
    result = json.loads(ams.aider_get_log())
    assert result['ok'] is True
    assert result['entries'] == []
    assert 'no audit log' in result['note']


def test_get_log_returns_last_n(tmp_path, monkeypatch):
    log = tmp_path / 'usage.csv'
    monkeypatch.setattr(ams, '_AUDIT_LOG', log)

    with log.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=ams._AUDIT_FIELDS)
        w.writeheader()
        for i in range(20):
            w.writerow({
                'timestamp': f'2026-06-15T00:00:{i:02d}',
                'mode': 'edit',
                'files': 'src/tgw/items.py',
                'prompt_excerpt': f'task {i}',
                'exit_code': '0',
                'duration_s': '1.0',
            })

    result = json.loads(ams.aider_get_log(5))
    assert result['ok'] is True
    assert len(result['entries']) == 5
    assert result['entries'][-1]['prompt_excerpt'] == 'task 19'
    assert result['count'] == 20


def test_get_log_capped_at_100(tmp_path, monkeypatch):
    log = tmp_path / 'usage.csv'
    monkeypatch.setattr(ams, '_AUDIT_LOG', log)
    with log.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=ams._AUDIT_FIELDS)
        w.writeheader()
        for i in range(5):
            w.writerow(dict.fromkeys(ams._AUDIT_FIELDS, str(i)))

    result = json.loads(ams.aider_get_log(999))
    assert len(result['entries']) == 5  # capped by actual rows, not by 100


# ---------------------------------------------------------------------------
# aider_get_diff
# ---------------------------------------------------------------------------


def test_get_diff_no_changes(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda cmd, **kw: _fake_proc(stdout=''))
    result = json.loads(ams.aider_get_diff())
    assert result['ok'] is True
    assert result['diff'] == '(no changes)'
    assert result['staged'] is False


def test_get_diff_with_changes(monkeypatch):
    diff_text = '--- a/items.py\n+++ b/items.py\n@@ -1 +1 @@\n+new line\n'
    monkeypatch.setattr(subprocess, 'run', lambda cmd, **kw: _fake_proc(stdout=diff_text))
    result = json.loads(ams.aider_get_diff())
    assert result['ok'] is True
    assert '+new line' in result['diff']
    assert result['truncated'] is False


def test_get_diff_staged_flag(monkeypatch):
    captured = []
    monkeypatch.setattr(
        subprocess, 'run',
        lambda cmd, **kw: captured.append(cmd) or _fake_proc(stdout='staged diff'),
    )
    result = json.loads(ams.aider_get_diff(staged=True))
    assert result['ok'] is True
    assert result['staged'] is True
    assert '--staged' in captured[0]


def test_get_diff_truncated(monkeypatch):
    big = 'd' * 20000
    monkeypatch.setattr(subprocess, 'run', lambda cmd, **kw: _fake_proc(stdout=big))
    result = json.loads(ams.aider_get_diff())
    assert result['truncated'] is True
    assert len(result['diff']) == 12000


# ---------------------------------------------------------------------------
# Tool count guard — catches drift if tools are added/removed
# ---------------------------------------------------------------------------


def test_tool_count():
    assert len([t for t in ('aider_run_task', 'aider_get_log', 'aider_get_diff')
                if hasattr(ams, t)]) == 3
