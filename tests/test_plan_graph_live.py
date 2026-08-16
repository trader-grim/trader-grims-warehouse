from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tgw.plan_graph import SourcePreconditionError, live_plan_graph


def _plan_repo(tmp_path: Path) -> Path:
    root = tmp_path / 'plans'
    (root / 'plan' / 'pp').mkdir(parents=True)
    (root / 'reference').mkdir()
    (root / 'plan' / 'TGW-Master-Plan.md').write_text(
        '# Master Plan\n\n## PP-ALPHA-001\nCanonical widget boundary. Governed by C12.\n'
    )
    (root / 'plan' / 'pp' / 'PP-ALPHA-001.md').write_text(
        '# PP-ALPHA-001\n\nThe widget boundary is exact.\n'
    )
    (root / 'reference' / 'invariants.md').write_text('# Invariants\n\n## C12\nPreserve boundary.\n')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run([
        'git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        'commit', '-qm', 'plan fixture',
    ], check=True)
    return root


def test_live_graph_binds_clean_standalone_commit_and_receiver(tmp_path):
    root = _plan_repo(tmp_path)
    result = live_plan_graph(root, 'PP-ALPHA-001', receiver='aider')
    assert result['ok'] is True
    assert result['plan_root'] == str(root)
    assert result['plan_commit'] == subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True,
    ).strip()
    assert len(result['source_envelope']) == 64
    assert result['receiver'] == 'aider'
    assert result['detailed_pp_documents']


def test_live_graph_refuses_dirty_plan_source(tmp_path):
    root = _plan_repo(tmp_path)
    (root / 'plan' / 'TGW-Master-Plan.md').write_text('# changed\n')
    with pytest.raises(SourcePreconditionError, match='source_changed'):
        live_plan_graph(root, 'PP-ALPHA-001')


def test_mcp_plan_graph_uses_configured_standalone_root(tmp_path, monkeypatch):
    from tgw import mcp_server

    root = _plan_repo(tmp_path)
    monkeypatch.setattr(mcp_server, '_cfg', {'standalone_plan_root': root})
    result = json.loads(mcp_server.tgw_get_plan_graph('PP-ALPHA-001'))
    assert result['ok'] is True
    assert result['plan_root'] == str(root)


def test_live_graph_rejects_unknown_receiver(tmp_path):
    with pytest.raises(ValueError, match='unknown receiver'):
        live_plan_graph(_plan_repo(tmp_path), 'PP-ALPHA-001', receiver='marketplace')


def test_live_plan_graph_uses_configured_git_executable(tmp_path):
    root = _plan_repo(tmp_path)
    wrapper = tmp_path / 'held-git'
    wrapper.write_text('#!/bin/sh\nexec git "$@"\n')
    wrapper.chmod(0o755)
    result = live_plan_graph(root, 'PP-ALPHA-001', git_path=str(wrapper))
    assert result['plan_commit']


def test_live_plan_graph_uses_explicit_disk_backed_runtime(tmp_path):
    root = _plan_repo(tmp_path)
    runtime = tmp_path / 'disk-runtime'
    result = live_plan_graph(root, 'PP-ALPHA-001', runtime_root=runtime)
    assert result['ok'] is True
    assert (runtime / 'tgw-plan-graph').is_dir()
