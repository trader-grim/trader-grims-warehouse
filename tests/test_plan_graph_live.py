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
    (root / 'plan' / 'execution').mkdir()
    (root / 'plan' / 'execution' / 'platform.yaml').write_text(
        'work_units:\n'
        '  - id: W10\n'
        '    title: Prove the canonical gate\n'
        '  - id: W11\n'
        '    title: Cut over consumers and retire bootstrap paths\n'
        '    requires: [W10]\n'
    )
    (root / 'reference' / 'invariants.md').write_text('# Invariants\n\n## C12\nPreserve boundary.\n')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run([
        'git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        'commit', '-qm', 'plan fixture',
    ], check=True)
    return root


def _binding(root: Path) -> dict[str, str]:
    return {
        'approved_plan_commit': subprocess.check_output(
            ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True,
        ).strip(),
        'approved_solution_hash': 'sha256:' + 'a' * 64,
    }


def _config_binding(root: Path) -> dict[str, str]:
    binding = _binding(root)
    return {
        'plan_approved_commit': binding['approved_plan_commit'],
        'plan_approved_solution_hash': binding['approved_solution_hash'],
    }


def test_live_graph_binds_clean_standalone_commit_and_receiver(tmp_path):
    root = _plan_repo(tmp_path)
    runtime = tmp_path / 'runtime'
    result = live_plan_graph(
        root, 'PP-ALPHA-001', receiver='aider', runtime_root=runtime, **_binding(root),
    )
    assert result['ok'] is True, result
    assert result['plan_root'] == str(root)
    assert result['plan_commit'] == subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True,
    ).strip()
    assert len(result['source_envelope']) == 64
    assert result['receiver'] == 'aider'
    assert result['detailed_pp_documents']
    projection = runtime / 'tgw-plan-graph' / result['source_envelope']
    assert projection.stat().st_mode & 0o777 == 0o555
    assert all(path.stat().st_mode & 0o777 == 0o444 for path in projection.iterdir())
    projection.chmod(0o700)


def test_live_graph_indexes_execution_work_unit_ids(tmp_path):
    root = _plan_repo(tmp_path)
    result = live_plan_graph(root, 'W11', **_binding(root))
    assert result['status'] == 'matched'
    assert result['candidates'][0]['node_id'] == 'work-unit:W11'
    assert result['candidates'][0]['citation']['path'] == 'plan/execution/platform.yaml'
    assert {
        'nodes': ['work-unit:W11', 'work-unit:W10'],
        'edges': [{
            'source': 'work-unit:W11',
            'relation': 'REQUIRES',
            'target': 'work-unit:W10',
            'anchor': 'plan/execution/platform.yaml:4',
        }],
    } in result['relationship_paths']


def test_live_graph_rejects_undeclared_execution_dependency(tmp_path):
    root = _plan_repo(tmp_path)
    (root / 'plan' / 'execution' / 'platform.yaml').write_text(
        'work_units:\n'
        '  - id: W11\n'
        '    title: Cut over consumers and retire bootstrap paths\n'
        '    requires: [W99]\n'
    )
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run([
        'git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        'commit', '-qm', 'invalid execution dependency',
    ], check=True)
    result = live_plan_graph(root, 'W11', operation='coverage', **_binding(root))
    assert result['stale'] is True
    assert {
        'path': 'plan/execution/platform.yaml',
        'reason': 'execution graph work unit requires an undeclared work unit',
    } in result['unreviewed']


def test_live_graph_refuses_dirty_plan_source(tmp_path):
    root = _plan_repo(tmp_path)
    (root / 'plan' / 'TGW-Master-Plan.md').write_text('# changed\n')
    with pytest.raises(SourcePreconditionError, match='source_changed'):
        live_plan_graph(root, 'PP-ALPHA-001', **_binding(root))


def test_mcp_plan_graph_uses_configured_standalone_root(tmp_path, monkeypatch):
    from tgw import mcp_server

    root = _plan_repo(tmp_path)
    monkeypatch.setattr(mcp_server, '_cfg', {'standalone_plan_root': root, **_config_binding(root)})
    result = json.loads(mcp_server.tgw_get_plan_graph('PP-ALPHA-001'))
    assert result['ok'] is True, result
    assert result['plan_root'] == str(root)


def test_live_graph_rejects_unknown_receiver(tmp_path):
    root = _plan_repo(tmp_path)
    with pytest.raises(ValueError, match='unknown receiver'):
        live_plan_graph(root, 'PP-ALPHA-001', receiver='marketplace', **_binding(root))


def test_live_graph_accepts_deepseek_review_receiver(tmp_path):
    root = _plan_repo(tmp_path)
    result = live_plan_graph(root, 'PP-ALPHA-001', receiver='deepseek', **_binding(root))
    assert result['receiver'] == 'deepseek'
    assert 'independently review evidence' in result['receiver_profile']


def test_live_plan_graph_uses_configured_git_executable(tmp_path):
    root = _plan_repo(tmp_path)
    wrapper = tmp_path / 'held-git'
    wrapper.write_text('#!/bin/sh\nexec git "$@"\n')
    wrapper.chmod(0o755)
    result = live_plan_graph(root, 'PP-ALPHA-001', git_path=str(wrapper), **_binding(root))
    assert result['plan_commit']


def test_live_graph_refuses_unpinned_clean_head(tmp_path):
    with pytest.raises(SourcePreconditionError, match='approved_plan_commit_required'):
        live_plan_graph(_plan_repo(tmp_path), 'PP-ALPHA-001')
