from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import tgw.workers.bundle_intake as bundle_intake
from tgw.workflow.listing_migration import (
    PHASE3_EXPLICIT_EXCLUSIONS,
    PHASE3_SUCCESSOR_INVENTORY,
    derive_bundle_downstream,
)


def _worker(item_root: Path, mode: str | None = None):
    worker = bundle_intake.BundleIntakeWorker.__new__(bundle_intake.BundleIntakeWorker)
    worker.config = {'itemdata_root': item_root}
    if mode is not None:
        worker.config['workflow_migration'] = {'bundle_downstream': mode}
    return worker


def _item(root: Path, sku: str, **changes) -> Path:
    document = {'sku': sku, 'image': 'one.jpg'}
    document.update(changes)
    path = root / sku / f'{sku}.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document), encoding='utf-8')
    return path


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bundle_intake.state_machine,
        'enqueue_catalog_rebuild',
        lambda *args, **kwargs: calls.append(('catalog_rebuild', args, kwargs)),
    )
    monkeypatch.setattr(
        bundle_intake.state_machine,
        'enqueue_job',
        lambda **kwargs: calls.append((kwargs['queue_name'], (), kwargs)) or 'job-id',
    )
    return calls


def test_workflow_decision_uses_authoritative_snapshot_and_skips_satisfied(tmp_path):
    pending = derive_bundle_downstream(_item(tmp_path, 'A'))
    satisfied = derive_bundle_downstream(
        _item(tmp_path, 'B', ebay_category_id='12345')
    )
    assert pending.object_id == 'A'
    assert pending.enqueue_ai_identify is True
    assert satisfied.enqueue_ai_identify is False
    assert pending.object_generation != satisfied.object_generation
    assert pending.graph_id != satisfied.graph_id


def test_legacy_rollback_is_exact_and_workflow_difference_is_generation_binding(tmp_path, monkeypatch):
    _item(tmp_path, 'A')
    calls = _capture(monkeypatch)
    _worker(tmp_path)._enqueue_downstream('A')
    legacy = list(calls)
    assert legacy[2][2] == {
        'queue_name': 'ai_identify', 'payload': {'sku': 'A'},
        'entity_type': 'item', 'entity_id': 'A',
        'dedupe_key': 'ai_identify:A', 'max_attempts': 3,
    }
    calls.clear()
    _worker(tmp_path, 'workflow')._enqueue_downstream('A')
    assert [call[0] for call in calls] == ['catalog_rebuild', 'thumbnail_gen', 'ai_identify']
    # Retained derived invalidations remain exact; the AI seam intentionally
    # changes to graph/generation-bound scheduler dispatch.
    assert calls[:2] == legacy[:2]
    assert calls[1][2] == {
        'queue_name': 'thumbnail_gen', 'payload': {'sku': 'A'},
        'entity_type': 'item', 'entity_id': 'A',
        'dedupe_key': 'thumbnail_gen:A', 'max_attempts': 3,
    }
    workflow = calls[2][2]
    assert workflow['queue_name'] == workflow['handler_family'] == 'ai_identify'
    assert workflow['entity_type'] == 'item' and workflow['entity_id'] == 'A'
    assert workflow['max_attempts'] == 3
    assert workflow['dedupe_key'] == (
        'treatment:ai_identify:item:A:'
        f"{workflow['payload']['object_generation']}:ai-identify:1"
    )
    assert workflow['payload']['sku'] == workflow['payload']['entity_id'] == 'A'
    assert workflow['payload']['treatment_id'] == 'ai-identify'
    assert workflow['payload']['treatment_version'] == '1'
    assert workflow['payload']['object_generation']
    assert workflow['payload']['condition_hash']
    assert workflow['payload']['fingerprints']
    assert workflow != legacy[2][2]


def test_workflow_satisfied_retains_only_derived_invalidations(tmp_path, monkeypatch):
    _item(tmp_path, 'A', ebay_category_id='12345')
    calls = _capture(monkeypatch)
    _worker(tmp_path, 'workflow')._enqueue_downstream('A')
    assert [call[0] for call in calls] == ['catalog_rebuild', 'thumbnail_gen']


def test_workflow_dispatch_failure_is_truthful_but_dedupe_is_success(tmp_path, monkeypatch):
    _item(tmp_path, 'A')
    calls = []
    monkeypatch.setattr(
        bundle_intake.state_machine,
        'enqueue_catalog_rebuild',
        lambda *args, **kwargs: calls.append('catalog_rebuild'),
    )

    def fail(**kwargs):
        if kwargs['queue_name'] == 'thumbnail_gen':
            return 'thumbnail-job'
        raise RuntimeError('queue unavailable')

    monkeypatch.setattr(bundle_intake.state_machine, 'enqueue_job', fail)
    with pytest.raises(RuntimeError, match='workflow dispatch failed'):
        _worker(tmp_path, 'workflow')._enqueue_downstream('A')

    class Duplicate(Exception):
        pgcode = '23505'

    def duplicate_ai(**kwargs):
        if kwargs['queue_name'] == 'thumbnail_gen':
            return 'thumbnail-job'
        raise Duplicate()

    monkeypatch.setattr(bundle_intake.state_machine, 'enqueue_job', duplicate_ai)
    # An existing job for the exact graph_id is idempotent parity success.
    _worker(tmp_path, 'workflow')._enqueue_downstream('A')


def test_selector_is_fail_closed_and_legacy_is_rollback_default(tmp_path, monkeypatch):
    _item(tmp_path, 'A', ebay_category_id='12345')
    calls = _capture(monkeypatch)
    with pytest.raises(bundle_intake.HardFailure, match='legacy.*workflow'):
        _worker(tmp_path, 'dual')._enqueue_downstream('A')
    assert calls == []
    _worker(tmp_path)._enqueue_downstream('A')
    assert [call[0] for call in calls] == ['catalog_rebuild', 'thumbnail_gen', 'ai_identify']


def test_selector_reads_normalized_config_raw_block(tmp_path, monkeypatch):
    _item(tmp_path, 'A', ebay_category_id='12345')
    calls = _capture(monkeypatch)
    worker = _worker(tmp_path)
    worker.config['raw'] = {'workflow_migration': {'bundle_downstream': 'workflow'}}
    worker._enqueue_downstream('A')
    assert [call[0] for call in calls] == ['catalog_rebuild', 'thumbnail_gen']


def _discovered_successor_candidates(root: Path):
    queue_targets = {
        'bundle_intake', 'multi_intake', 'thumbnail_gen', 'ai_identify',
        'ebay_draft', 'ebay_price', 'ebay_upload', 'ebay_stage',
        'ebay_publish', 'ebay_sync', 'ebay_dole',
    }
    catalog_listing_files = {
        'src/tgw/workers/bundle_intake.py',
        'src/tgw/workers/ebay_price.py',
        'src/tgw/workers/ebay_publish.py',
    }
    found = set()
    for path in (root / 'src/tgw').rglob('*.py'):
        relative = str(path.relative_to(root))
        tree = ast.parse(path.read_text(encoding='utf-8'))
        constants = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
            for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
                call_name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, 'id', '')
                target = None
                if call_name == 'enqueue_catalog_rebuild' and relative in catalog_listing_files:
                    target = 'catalog_rebuild'
                elif call_name == 'enqueue_post_push_sync':
                    target = 'ebay_sync'
                elif call_name == 'cmd_publish':
                    target = 'ebay_publish'
                elif call_name == 'enqueue_job':
                    keyword = next((item for item in call.keywords if item.arg == 'queue_name'), None)
                    if keyword and isinstance(keyword.value, ast.Constant):
                        target = keyword.value.value
                    elif keyword and isinstance(keyword.value, ast.Name):
                        target = constants.get(keyword.value.id, '<dynamic>')
                    if target == '<dynamic>' and relative == 'src/tgw/api.py' and function.name == 'cmd_enqueue_sku':
                        pass
                    elif target not in queue_targets:
                        target = None
                if target:
                    found.add((relative, function.name, target))
    return found


def test_repo_wide_successor_discovery_has_exact_classified_coverage():
    """A newly introduced listing successor fails until classified or excluded."""
    root = Path(__file__).parents[1]
    assert len(PHASE3_SUCCESSOR_INVENTORY) == len(set(PHASE3_SUCCESSOR_INVENTORY))
    assert {item[3] for item in PHASE3_SUCCESSOR_INVENTORY} == {
        'migrate', 'retained-derived', 'entrypoint-authority', 'scheduler-timer',
    }
    classified = {(path, function, target) for path, function, target, _ in PHASE3_SUCCESSOR_INVENTORY}
    excluded = {(path, function, target) for path, function, target, _ in PHASE3_EXPLICIT_EXCLUSIONS}
    discovered = _discovered_successor_candidates(root)
    assert discovered == classified | excluded
    assert not classified.intersection(excluded)


def test_already_migrated_workers_do_not_regain_successor_enqueues():
    workers = Path(__file__).parents[1] / 'src/tgw/workers'
    forbidden = {
        'ai_identify.py': ('ebay_draft', 'ebay_upload', 'ebay_price'),
        'ebay_draft.py': ('ebay_upload', 'ebay_price', 'ebay_stage'),
        'ebay_price.py': ('ebay_stage',),
        'ebay_stage.py': ('ebay_publish',),
    }
    for filename, queues in forbidden.items():
        source = (workers / filename).read_text(encoding='utf-8')
        for queue in queues:
            assert f"queue_name='{queue}'" not in source
            assert f'queue_name="{queue}"' not in source
