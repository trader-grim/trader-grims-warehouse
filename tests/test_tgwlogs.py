"""Tests for the tgwlogs MC extfs VFS (PP-MC-001 Phase 4).

The extfs script has no .py extension, so we load it by path with importlib.
Coverage focuses on the read-only contract: the worker allowlist guard (no
shell-injection surface), output capping, and the unit-name mapping.
"""

import importlib.machinery
import importlib.util
import subprocess
import types
from pathlib import Path

import pytest

_SCRIPT = (Path(__file__).resolve().parent.parent
           / 'etc/interfaces/mc/system/extfs.d/tgwlogs')


@pytest.fixture(scope='module')
def mod():
    # No .py extension → give importlib an explicit source loader.
    loader = importlib.machinery.SourceFileLoader('tgwlogs_vfs', str(_SCRIPT))
    spec = importlib.util.spec_from_loader('tgwlogs_vfs', loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def test_unit_name(mod):
    assert mod._unit('ai_identify') == 'tgw-worker@ai_identify.service'


def test_lines_cap_default_and_clamp(mod, monkeypatch):
    monkeypatch.delenv('TGWLOGS_LINES', raising=False)
    assert mod._lines_cap() == 500
    monkeypatch.setenv('TGWLOGS_LINES', '99999')
    assert mod._lines_cap() == mod._MAX_LINES
    monkeypatch.setenv('TGWLOGS_LINES', 'garbage')
    assert mod._lines_cap() == 500


def test_journal_rejects_unknown_queue_without_subprocess(mod, monkeypatch):
    # An unknown queue must never reach subprocess at all.
    monkeypatch.setattr(subprocess, 'run',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('ran journalctl')))
    out = mod._journal('foo; rm -rf /')
    assert 'unknown worker queue' in out


def test_journal_uses_argv_list_and_caps(mod, monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        captured['cmd'] = cmd
        return types.SimpleNamespace(returncode=0, stdout='line1\nline2\n', stderr='')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setenv('TGWLOGS_LINES', '42')
    out = mod._journal('ai_identify')
    assert out == 'line1\nline2\n'
    cmd = captured['cmd']
    assert isinstance(cmd, list)                       # never shell=True
    assert cmd[0] == 'journalctl'
    assert 'tgw-worker@ai_identify.service' in cmd
    assert '42' in cmd                                  # cap honored


def test_journal_permission_hint_on_failure(mod, monkeypatch):
    monkeypatch.setattr(subprocess, 'run',
                        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout='', stderr='denied'))
    out = mod._journal('echo')
    assert 'systemd-journal' in out


def test_content_for_summary(mod, monkeypatch):
    monkeypatch.setattr(mod, '_is_active', lambda q: 'active')
    out = mod._content_for('_summary.txt')
    assert 'TGW worker journal portal' in out
    assert 'ai_identify' in out


def test_content_for_log_strips_suffix(mod, monkeypatch):
    seen = {}
    monkeypatch.setattr(mod, '_journal', lambda q: seen.setdefault('q', q) or 'ok')
    mod._content_for('ebay_draft.log')
    assert seen['q'] == 'ebay_draft'
