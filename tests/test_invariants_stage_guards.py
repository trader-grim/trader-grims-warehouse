"""Invariants C1/C2 (docs/invariants.md) — ebay_stage guard rails.

Never stage with: an Active listing, an unresolved legacy listing, an existing
offer (idempotency), a missing draft, a null price, or zero photos. Staging
must end at UNPUBLISHED — never publish.

Also cross-checks invariant D6: the "no photos yet" error string raised here
must keep classifying as a transient requeue in worker_base, because the two
are coupled only by substring match.

Worker built via object.__new__ to skip the DB-touching __init__
(pattern from tests/test_strikethrough.py).
"""

import json

import pytest

import tgw.workers.ebay_stage as ebay_stage
from tgw.queue.worker_base import HardFailure, classify_dead_letter


@pytest.fixture
def stage(tmp_path, monkeypatch):
    """(worker, staged_calls) with stage_draft and all side effects stubbed."""
    monkeypatch.setattr(ebay_stage.tgw_logging, 'log_event', lambda *a, **k: None)
    enqueued = []
    monkeypatch.setattr(ebay_stage.state_machine, 'active_jobs_for_sku',
                        lambda sku, queues: [])
    monkeypatch.setattr(ebay_stage.state_machine, 'enqueue_job',
                        lambda **kw: enqueued.append(kw))
    calls = []

    def fake_stage_draft(cfg, sku, item):
        calls.append(sku)
        return {'offer_id': 'OFF-NEW', 'status': 'UNPUBLISHED', 'inventory_item': {}}

    monkeypatch.setattr(ebay_stage, 'stage_draft', fake_stage_draft)

    worker = object.__new__(ebay_stage.EbayStageWorker)
    from tests.conftest import make_fake_fence_write, make_fake_patch_item
    monkeypatch.setattr(ebay_stage, 'fence_ebay_write', make_fake_fence_write(tmp_path))
    monkeypatch.setattr(ebay_stage, 'fence_patch_item', make_fake_patch_item(tmp_path))
    worker.config = {'itemdata_root': tmp_path, 'pretty': False, 'api_key': 'test-api-key'}
    worker._staged = calls
    worker._enqueued = enqueued
    return worker


def _write(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir(parents=True)
    path = d / f'{sku}.json'
    path.write_text(json.dumps(item), encoding='utf-8')
    return path


def _ready_item(**extra):
    """An item that passes every guard (epid set so the EPID lookup is skipped)."""
    item = {
        'title': 'Acme Thing',
        'epid':  '12345678',
        'draft_listing': {'title': 'Acme Thing', 'category_id': '12345',
                          'price': 9.99, 'imageUrls': ['https://eps/1.jpg']},
        'ebay_offer': {'price': 9.99, 'price_comps': {'count': 3, 'p25': 9.99}},
    }
    item.update(extra)
    return item


def _run(worker, sku):
    worker.handle({'payload_json': {'sku': sku}})


# ---------------------------------------------------------------------------
# C1 — guards, in order
# ---------------------------------------------------------------------------

def test_active_listing_never_staged(stage, tmp_path):
    item = _ready_item(ebay_listing={'status': 'Active', 'listing_id': '110001'})
    path = _write(tmp_path, 'tgw1', item)
    before = path.read_text()
    _run(stage, 'tgw1')
    assert stage._staged == []
    assert path.read_text() == before   # skip writes nothing


def test_unresolved_legacy_listing_never_staged(stage, tmp_path):
    _write(tmp_path, 'tgw2', _ready_item(**{'Item number': '110000012345'}))
    _run(stage, 'tgw2')
    assert stage._staged == []


def test_resolved_legacy_listing_may_stage(stage, tmp_path):
    _write(tmp_path, 'tgw3', _ready_item(**{'Item number': '110000012345',
                                            'legacy_listing_resolved': True}))
    _run(stage, 'tgw3')
    assert stage._staged == ['tgw3']


def test_existing_offer_skips_idempotently(stage, tmp_path):
    item = _ready_item()
    item['ebay_offer']['offer_id'] = 'OFF-EXISTING'
    _write(tmp_path, 'tgw4', item)
    _run(stage, 'tgw4')
    assert stage._staged == []


def test_missing_draft_is_retryable_not_fatal(stage, tmp_path):
    _write(tmp_path, 'tgw5', {'title': 'Acme'})
    with pytest.raises(RuntimeError, match='no draft_listing'):
        _run(stage, 'tgw5')
    assert stage._staged == []


def test_null_price_is_retryable_and_blocks_staging(stage, tmp_path):
    item = _ready_item()
    item['draft_listing'].pop('price')
    item['ebay_offer'].pop('price')
    _write(tmp_path, 'tgw6', item)
    with pytest.raises(RuntimeError, match='no price'):
        _run(stage, 'tgw6')
    assert stage._staged == []


def test_zero_photos_blocks_staging_and_classifies_transient(stage, tmp_path):
    item = _ready_item()
    item['draft_listing'].pop('imageUrls')
    _write(tmp_path, 'tgw7', item)
    with pytest.raises(RuntimeError) as excinfo:
        _run(stage, 'tgw7')
    assert stage._staged == []
    # D6 cross-check: this exact wording must keep matching _TRANSIENT_ERRORS,
    # otherwise the job dead-letters instead of waiting for ebay_upload.
    action, delay = classify_dead_letter(repr(excinfo.value))
    assert (action, delay) == ('requeue', 600)


def test_missing_item_json_is_hard_failure(stage):
    with pytest.raises(HardFailure):
        _run(stage, 'tgw-nope')


def test_ebay_validation_error_becomes_hard_failure(stage, tmp_path, monkeypatch):
    _write(tmp_path, 'tgw8', _ready_item())
    monkeypatch.setattr(ebay_stage, 'stage_draft',
                        lambda cfg, sku, item: (_ for _ in ()).throw(
                            ValueError('tgw8: no price set')))
    with pytest.raises(HardFailure):
        _run(stage, 'tgw8')


# ---------------------------------------------------------------------------
# C2 — staging ends UNPUBLISHED, preserves pricing provenance
# ---------------------------------------------------------------------------

def test_successful_stage_is_unpublished_and_preserves_comps(stage, tmp_path):
    path = _write(tmp_path, 'tgw9', _ready_item())
    _run(stage, 'tgw9')
    after = json.loads(path.read_text(encoding='utf-8'))
    offer = after['ebay_offer']
    assert offer['offer_id'] == 'OFF-NEW'
    assert offer['status'] == 'UNPUBLISHED'
    assert offer['staged_at']
    assert offer['price_comps'] == {'count': 3, 'p25': 9.99}  # merge, not replace
    assert 'ebay_listing' not in after                        # never published
    assert any(kw['queue_name'] == 'catalog_rebuild' for kw in stage._enqueued)

# ---------------------------------------------------------------------------
# C5-extended — never-raise clamp on force re-stage (session 42 incident)
# ---------------------------------------------------------------------------

def _run_force(worker, sku, **extra_payload):
    # operator origin by default — C9 blocks operator-less force on live items
    payload = {'sku': sku, 'force': True, 'origin': 'operator', **extra_payload}
    worker.handle({'payload_json': payload})


def _live_item(draft_price, offer_price):
    item = _ready_item()
    item['draft_listing']['price'] = draft_price
    item['ebay_offer'].update({'price': offer_price, 'offer_id': 'OFF-1',
                               'status': 'PUBLISHED'})
    return item


def test_force_restage_never_raises_live_price(stage, tmp_path):
    # Stale pre-s41 draft price above the live markdown must be clamped to the
    # live price AND persisted back (heals the stale draft, price_history event).
    path = _write(tmp_path, 'tgw10', _live_item(draft_price=9.97, offer_price=7.98))
    _run_force(stage, 'tgw10')
    assert stage._staged == ['tgw10']
    after = json.loads(path.read_text(encoding='utf-8'))
    assert after['draft_listing']['price'] == 7.98
    ev = after['price_history'][-1]
    assert ev['label'] == 'never_raise_clamp'
    assert ev['previous_price'] == 9.97


def test_force_restage_allows_operator_authorized_raise(stage, tmp_path):
    path = _write(tmp_path, 'tgw11', _live_item(draft_price=12.00, offer_price=7.98))
    _run_force(stage, 'tgw11', allow_price_raise=True)
    assert stage._staged == ['tgw11']
    after = json.loads(path.read_text(encoding='utf-8'))
    assert after['draft_listing']['price'] == 12.00
    assert not after.get('price_history')


def test_force_restage_lowering_passes_unclamped(stage, tmp_path):
    # A pending reduction (draft below live) is the reducer doing its job.
    path = _write(tmp_path, 'tgw12', _live_item(draft_price=6.50, offer_price=7.98))
    _run_force(stage, 'tgw12')
    assert stage._staged == ['tgw12']
    after = json.loads(path.read_text(encoding='utf-8'))
    assert after['draft_listing']['price'] == 6.50


def test_nonforce_unpublished_stage_not_clamped(stage, tmp_path):
    # First-time staging of a fresh item is untouched by the guard.
    _write(tmp_path, 'tgw13', _ready_item())
    _run(stage, 'tgw13')
    assert stage._staged == ['tgw13']


# ---------------------------------------------------------------------------
# C9 — uninspected AI content never reaches a live listing (session 42)
# ---------------------------------------------------------------------------

def test_force_on_live_listing_without_operator_origin_is_blocked(stage, tmp_path):
    path = _write(tmp_path, 'tgw14', _live_item(draft_price=9.99, offer_price=9.99))
    before = path.read_text()
    stage.handle({'payload_json': {'sku': 'tgw14', 'force': True}})
    assert stage._staged == []               # refused — no PUT
    assert path.read_text() == before        # and nothing written


def test_force_on_unpublished_offer_passes_without_origin(stage, tmp_path):
    # Pre-publish price-drift force re-stage (ebay_publish deadlock-breaker)
    # targets an UNPUBLISHED offer — C9 does not apply.
    item = _ready_item()
    item['ebay_offer'].update({'offer_id': 'OFF-2', 'status': 'UNPUBLISHED'})
    _write(tmp_path, 'tgw15', item)
    stage.handle({'payload_json': {'sku': 'tgw15', 'force': True}})
    assert stage._staged == ['tgw15']
