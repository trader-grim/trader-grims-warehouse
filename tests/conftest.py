"""Shared test helpers and fixtures for TGW test suite."""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _pin_coding_executors(monkeypatch):
    """Default the coding executor selection to codex for the whole suite.

    The committed config/model-availability.json marks codex unavailable (its
    real state), which would route every review/implement test through the
    claude/manual backend. Tests that mean to exercise a specific executor set
    TGW_IMPLEMENT_EXECUTOR / TGW_REVIEW_EXECUTOR (or TGW_MODEL_AVAILABILITY)
    themselves; the model selector itself is covered by test_model_selector.py.
    """
    monkeypatch.delenv("TGW_MODEL_AVAILABILITY", raising=False)
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "codex")
    monkeypatch.setenv("TGW_REVIEW_EXECUTOR", "codex")
    try:
        from tgw.workers import codex_implement

        codex_implement._SELECTION_CACHE.clear()
    except Exception:  # pragma: no cover - import-order safety only
        pass


@pytest.fixture
def durable_path():
    """A small disposable root for code that correctly rejects ``/tmp``.

    W18 receipts, leases, watched inputs, and refresh state are deliberately
    required to live on durable storage.  Pytest's built-in ``tmp_path`` is
    therefore the wrong fixture for those boundaries.  CI may select an exact
    durable test filesystem with ``TGW_TEST_DURABLE_ROOT``; ``/var/tmp`` is the
    portable fallback and every per-test directory is removed afterward.
    """

    base = Path(os.environ.get("TGW_TEST_DURABLE_ROOT", "/var/tmp/tgw-pytest"))
    if not base.is_absolute() or base == Path("/tmp") or Path("/tmp") in base.parents:
        raise RuntimeError("TGW_TEST_DURABLE_ROOT must be absolute and outside /tmp")
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="w18-", dir=base))
    try:
        yield root
    finally:
        shutil.rmtree(root)


def make_fake_fence_write(itemdata_root):
    """Return a fake fence_ebay_write that merges eBay blocks into the item JSON on disk.

    Simulates what the fence server does in production, so tests that read
    the item JSON back after worker.handle() see the expected fields.
    """
    def fake_fence_ebay_write(cfg, sku, ebay_offer=None, ebay_listing=None,
                              ebay_submitted=None, ebay_live=None,
                              allow_protected=None, expected_generation=None):
        root = Path(cfg.get('itemdata_root', itemdata_root))
        p = root / sku / f'{sku}.json'
        doc = json.loads(p.read_text(encoding='utf-8'))
        from tgw.item_mutation import item_generation
        observed_generation = item_generation(doc)
        if (expected_generation is not None
                and observed_generation != expected_generation):
            raise RuntimeError(
                'generation conflict: '
                f'expected {expected_generation}, observed {observed_generation}'
            )
        for key, val in [('ebay_offer', ebay_offer), ('ebay_listing', ebay_listing),
                         ('ebay_submitted', ebay_submitted), ('ebay_live', ebay_live)]:
            if val is not None:
                doc[key] = {**doc.get(key, {}), **val}
        p.write_text(json.dumps(doc), encoding='utf-8')
        return {'ok': True, 'resulting_generation': item_generation(doc)}
    return fake_fence_ebay_write


def make_fake_patch_item(itemdata_root):
    """Return a fake fence_patch_item that merges top-level fields into item JSON."""
    def fake_fence_patch_item(cfg, sku, fields, *, expected_generation=None):
        root = Path(cfg.get('itemdata_root', itemdata_root))
        p = root / sku / f'{sku}.json'
        doc = json.loads(p.read_text(encoding='utf-8'))
        from tgw.item_mutation import item_generation
        observed_generation = item_generation(doc)
        if (expected_generation is not None
                and observed_generation != expected_generation):
            raise RuntimeError(
                'generation conflict: '
                f'expected {expected_generation}, observed {observed_generation}'
            )
        doc.update(fields)
        p.write_text(json.dumps(doc), encoding='utf-8')
        return {'ok': True, 'resulting_generation': item_generation(doc)}
    return fake_fence_patch_item


def make_fake_sold_evidence(itemdata_root):
    """Return a fake fence.sold_evidence that applies the sold-order evidence
    set to the item JSON on disk, mirroring http_server._apply_sold_evidence
    (PP-SOLD-001 / Todo #1966). Draft content other than quantity is untouched.
    """
    def fake_fence_sold_evidence(cfg, sku, *, ebay_sale, sold_out=False,
                                 remaining_quantity=None):
        root = Path(cfg.get('itemdata_root', itemdata_root))
        p = root / sku / f'{sku}.json'
        doc = json.loads(p.read_text(encoding='utf-8'))
        doc['ebay_sale'] = list(ebay_sale)
        if sold_out:
            doc['status'] = 'sold'
            existing_listing = doc.get('ebay_listing')
            if not isinstance(existing_listing, dict):
                existing_listing = {}
            doc['ebay_listing'] = {**existing_listing, 'status': 'Sold'}
        target_quantity = 0 if sold_out else remaining_quantity
        if target_quantity is not None:
            existing_draft = doc.get('draft_listing')
            if not isinstance(existing_draft, dict):
                existing_draft = {}
            doc['draft_listing'] = {**existing_draft, 'quantity': target_quantity}
        p.write_text(json.dumps(doc), encoding='utf-8')
        from tgw.item_mutation import item_generation
        return {'ok': True, 'sku': sku, 'resulting_generation': item_generation(doc)}
    return fake_fence_sold_evidence


def make_fake_sold_evidence_tmp(tmp_path):
    """Like make_fake_sold_evidence but resolves sku path via tmp_path directly."""
    import json
    from pathlib import Path

    def fake_fence_sold_evidence(cfg, sku, *, ebay_sale, sold_out=False,
                                 remaining_quantity=None):
        root = cfg.get('itemdata_root') or tmp_path
        p = Path(root) / sku / f'{sku}.json'
        if not p.exists():
            p = Path(tmp_path) / sku / f'{sku}.json'
        if not p.exists():
            return {'ok': True, 'sku': sku}
        doc = json.loads(p.read_text(encoding='utf-8'))
        doc['ebay_sale'] = list(ebay_sale)
        if sold_out:
            doc['status'] = 'sold'
            existing_listing = doc.get('ebay_listing')
            if not isinstance(existing_listing, dict):
                existing_listing = {}
            doc['ebay_listing'] = {**existing_listing, 'status': 'Sold'}
        target_quantity = 0 if sold_out else remaining_quantity
        if target_quantity is not None:
            existing_draft = doc.get('draft_listing')
            if not isinstance(existing_draft, dict):
                existing_draft = {}
            doc['draft_listing'] = {**existing_draft, 'quantity': target_quantity}
        p.write_text(json.dumps(doc), encoding='utf-8')
        from tgw.item_mutation import item_generation
        return {'ok': True, 'sku': sku, 'resulting_generation': item_generation(doc)}
    return fake_fence_sold_evidence


def make_fake_create_item(itemdata_root):
    """Return a fake fence_create_item that writes the item JSON directly to
    disk under itemdata_root, mirroring what the real fence server does —
    for testing worker code that calls tgw.apis.fence.create_item without
    a live http_server.
    """
    def fake_fence_create_item(cfg, sku, data):
        root = Path(cfg.get('itemdata_root', itemdata_root))
        d = root / sku
        d.mkdir(parents=True, exist_ok=True)
        p = d / f'{sku}.json'
        record = {'sku': sku, **data}
        p.write_text(json.dumps(record), encoding='utf-8')
        return {'ok': True, 'sku': sku, 'path': str(p)}
    return fake_fence_create_item


def make_fake_fence_write_tmp(tmp_path):
    """Like make_fake_fence_write but resolves sku path via tmp_path directly."""
    import json
    from pathlib import Path

    def fake_fence_ebay_write(cfg, sku, ebay_offer=None, ebay_listing=None,
                              ebay_submitted=None, ebay_live=None,
                              allow_protected=None, expected_generation=None):
        # Try cfg['itemdata_root'] first, then fall back to tmp_path
        root = cfg.get('itemdata_root') or tmp_path
        p = Path(root) / sku / f'{sku}.json'
        if not p.exists():
            # Fallback: try tmp_path directly
            p = Path(tmp_path) / sku / f'{sku}.json'
        if p.exists():
            doc = json.loads(p.read_text(encoding='utf-8'))
            from tgw.item_mutation import item_generation
            observed_generation = item_generation(doc)
            if (expected_generation is not None
                    and observed_generation != expected_generation):
                raise RuntimeError(
                    'generation conflict: '
                    f'expected {expected_generation}, observed {observed_generation}'
                )
            for key, val in [('ebay_offer', ebay_offer), ('ebay_listing', ebay_listing),
                             ('ebay_submitted', ebay_submitted), ('ebay_live', ebay_live)]:
                if val is not None:
                    doc[key] = {**doc.get(key, {}), **val}
            p.write_text(json.dumps(doc), encoding='utf-8')
            return {'ok': True, 'resulting_generation': item_generation(doc)}
        return {'ok': True}
    return fake_fence_ebay_write


def make_fake_patch_item_tmp(tmp_path):
    """Like make_fake_patch_item but resolves sku path via tmp_path directly."""
    import json
    from pathlib import Path

    def fake_fence_patch_item(cfg, sku, fields, *, expected_generation=None):
        root = cfg.get('itemdata_root') or tmp_path
        p = Path(root) / sku / f'{sku}.json'
        if not p.exists():
            p = Path(tmp_path) / sku / f'{sku}.json'
        if p.exists():
            doc = json.loads(p.read_text(encoding='utf-8'))
            doc.update(fields)
            p.write_text(json.dumps(doc), encoding='utf-8')
            from tgw.item_mutation import item_generation
            return {'ok': True, 'resulting_generation': item_generation(doc)}
        return {'ok': True}
    return fake_fence_patch_item


def make_governed_ebay_job(itemdata_root, sku, *, treatment_id, **payload_extra):
    """Construct the smallest real workflow-bound eBay worker job.

    Old tests used unbound direct queue payloads.  Those are no longer a
    production mode, so behavioral tests must exercise the same generation
    and authority envelope as the evaluator-created job.
    """
    root = Path(itemdata_root)
    from tgw.item_mutation import item_generation
    item_path = root / sku / f'{sku}.json'
    item = json.loads(item_path.read_text(encoding='utf-8')) if item_path.is_file() else None
    payload = {
        'sku': sku,
        'entity_id': sku,
        'object_id': sku,
        'treatment_id': treatment_id,
        'treatment_version': '1',
        'graph_id': 'test-governed-graph',
        'goal_profile_id': 'test-goal',
        'goal_profile_version': '1',
        'object_generation': item_generation(item) if item is not None else '0' * 64,
        'condition_hash': 'test-condition',
        'operator_authority_id': 'test-authority',
        'pre_authority_condition_hash': 'test-pre-authority-condition',
        **payload_extra,
    }
    return {
        'job_id': '00000000-0000-4000-8000-000000000001',
        'lease_token': '00000000-0000-4000-8000-000000000002',
        'entity_type': 'item',
        'entity_id': sku,
        'payload_json': payload,
        'attempt_count': 0,
        'max_attempts': 3,
    }
