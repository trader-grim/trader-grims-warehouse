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
from types import SimpleNamespace

import pytest

import tgw.provider_effects as provider_effects
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
    monkeypatch.setattr(
        ebay_stage,
        'validate_listing_condition_for_stage',
        lambda *args, **kwargs: 'USED_GOOD',
    )
    monkeypatch.setattr(ebay_stage, 'enqueue_post_push_sync', lambda *a, **k: True)
    monkeypatch.setattr(
        provider_effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: SimpleNamespace(
            effect_id='test-stage-effect', state='dispatched', result=None),
    )
    monkeypatch.setattr(
        provider_effects, 'finish_provider_effect',
        lambda effect_id, **kwargs: SimpleNamespace(
            effect_id=effect_id, state=kwargs['state'], result=kwargs.get('result')),
    )

    worker = object.__new__(ebay_stage.EbayStageWorker)
    from tests.conftest import make_fake_fence_write, make_fake_patch_item
    monkeypatch.setattr(ebay_stage, 'fence_ebay_write', make_fake_fence_write(tmp_path))
    monkeypatch.setattr(ebay_stage, 'fence_patch_item', make_fake_patch_item(tmp_path))
    worker.config = {
        'itemdata_root': tmp_path, 'pretty': False, 'api_key': 'test-api-key',
        'workflow_migration': {
            'ebay_stage_provider_effect': 'workflow',
            'ebay_provider_identity': 'test-seller',
        },
    }
    worker._staged = calls
    worker._enqueued = enqueued
    return worker


def _write(tmp_path, sku, item):
    item = {'sku': sku, **item}
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


def _job(worker, sku, **payload_extra):
    from tests.conftest import make_governed_ebay_job
    return make_governed_ebay_job(
        worker.config['itemdata_root'], sku,
        treatment_id='ebay-stage', **payload_extra,
    )


def _run(worker, sku, **payload_extra):
    return worker.handle(_job(worker, sku, **payload_extra))


# ---------------------------------------------------------------------------
# C1 — guards, in order
# ---------------------------------------------------------------------------

def test_active_listing_never_staged(stage, tmp_path):
    item = _ready_item(ebay_listing={'status': 'Active', 'listing_id': '110001'})
    path = _write(tmp_path, 'tgw1', item)
    before = path.read_text()
    with pytest.raises(ebay_stage.TreatmentFailure) as caught:
        _run(stage, 'tgw1')
    assert caught.value.result['evidence']['reason_code'] == 'ACTIVE_LISTING_REQUIRES_FORCE'
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


def test_existing_offer_without_bound_effect_requires_reconciliation(stage, tmp_path, monkeypatch):
    item = _ready_item()
    item['ebay_offer']['offer_id'] = 'OFF-EXISTING'
    _write(tmp_path, 'tgw4', item)
    monkeypatch.setattr(
        provider_effects, 'validate_succeeded_authorized_effect',
        lambda **kwargs: (_ for _ in ()).throw(
            provider_effects.ProviderEffectConflict('missing bound effect')),
    )
    with pytest.raises(ebay_stage.TreatmentFailure) as caught:
        _run(stage, 'tgw4')
    assert caught.value.result['evidence']['reason_code'] == 'PROVIDER_EFFECT_REPLAY_INVALID'
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
    # PP-CATALOG-INCR-001 CI-4 (2026-07-18): catalog_rebuild's enqueue is now
    # a no-op — the SQLite catalog stays live via CI-2's synchronous
    # fence-write upsert instead.
    assert all(kw['queue_name'] != 'catalog_rebuild' for kw in stage._enqueued)

# ---------------------------------------------------------------------------
# C5-extended — never-raise clamp on force re-stage (session 42 incident)
# ---------------------------------------------------------------------------

def _run_force(worker, sku, **extra_payload):
    # operator origin by default — C9 blocks operator-less force on live items
    payload = {'sku': sku, 'force': True, 'origin': 'operator', **extra_payload}
    return worker.handle(
        _job(worker, sku, **{
            key: value for key, value in payload.items() if key != 'sku'
        })
    )


def _live_item(draft_price, offer_price):
    item = _ready_item()
    item['draft_listing']['price'] = draft_price
    item['ebay_offer'].update({'price': offer_price, 'offer_id': 'OFF-1',
                               'status': 'PUBLISHED'})
    return item


def test_force_restage_never_raises_live_price(stage, tmp_path):
    # Stale pre-s41 draft price above the live markdown must be clamped to the
    # live price AND persisted back (heals the stale draft, price_history event).
    # The repair changes the governed generation, so provider dispatch must wait
    # for a new evaluation/authority instead of falling through on stale intent.
    path = _write(tmp_path, 'tgw10', _live_item(draft_price=9.97, offer_price=7.98))
    receipt = _run_force(stage, 'tgw10')
    assert stage._staged == []
    assert receipt['outcome'] == 'partial'
    assert receipt['evidence']['reason_code'] == 'NEVER_RAISE_CLAMP_APPLIED'
    after = json.loads(path.read_text(encoding='utf-8'))
    assert after['draft_listing']['price'] == 7.98
    ev = after['price_history'][-1]
    assert ev['label'] == 'never_raise_clamp'
    assert ev['previous_price'] == 9.97
    from tgw.item_mutation import item_generation
    assert receipt['evidence']['resulting_generation'] == item_generation(after)
    assert receipt['object_generation'] != item_generation(after)


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
    with pytest.raises(ebay_stage.TreatmentFailure) as caught:
        _run(stage, 'tgw14', force=True)
    assert caught.value.result['evidence']['reason_code'] == 'OPERATOR_ORIGIN_REQUIRED'
    assert stage._staged == []               # refused — no PUT
    assert path.read_text() == before        # and nothing written


def test_force_on_unpublished_offer_passes_without_origin(stage, tmp_path):
    # Pre-publish price-drift force re-stage (ebay_publish deadlock-breaker)
    # targets an UNPUBLISHED offer — C9 does not apply.
    item = _ready_item()
    item['ebay_offer'].update({'offer_id': 'OFF-2', 'status': 'UNPUBLISHED'})
    _write(tmp_path, 'tgw15', item)
    _run(stage, 'tgw15', force=True)
    assert stage._staged == ['tgw15']


# ---------------------------------------------------------------------------
# PP-PHOTOSYNC-001 P10 (session 43) — legacy-listing skip must be persisted
# durably (not just logged), and repaired in-place when operator-driven.
# ---------------------------------------------------------------------------

def _legacy_item(**extra):
    item = _ready_item(**{'Item number': '110000012345',
                          'ebay_listing': {'status': 'Active', 'listing_id': '226700000001'}})
    item.update(extra)
    return item


def test_legacy_skip_persists_durably_even_without_operator_origin(stage, tmp_path):
    """The core data-loss fix: a legacy skip must land in the item JSON, not
    just journald — regardless of whether a duplicate check was attempted."""
    _write(tmp_path, 'tgw20', _legacy_item())
    _run(stage, 'tgw20', force=True, origin='operator')
    after = json.loads((tmp_path / 'tgw20' / 'tgw20.json').read_text(encoding='utf-8'))
    assert after['legacy_listing_blocked']['item_number'] == '110000012345'
    assert after['legacy_listing_blocked']['listing_id'] == '226700000001'


def test_legacy_duplicate_check_only_attempted_with_operator_origin(stage, tmp_path, monkeypatch):
    """C9 applies to this check exactly like the Inventory-API path: a
    background (no-origin) job may record the finding but must never make a
    live eBay-state decision on its own."""
    check_calls = []
    import tgw.ebay.pull as pull_mod
    monkeypatch.setattr(pull_mod, 'check_legacy_duplicate_listing',
                        lambda cfg, sku, listing_id: check_calls.append(sku) or {'ok': True, 'match': True})

    _write(tmp_path, 'tgw21', _legacy_item())
    _run(stage, 'tgw21', force=True)  # no origin
    assert check_calls == []
    after = json.loads((tmp_path / 'tgw21' / 'tgw21.json').read_text(encoding='utf-8'))
    assert after['legacy_listing_blocked']['duplicate_check'] is None
    assert 'legacy_listing_resolved' not in after


def test_legacy_confirmed_not_duplicate_resolves_and_falls_through(stage, tmp_path, monkeypatch):
    """Dave, s43: 'check for both specifically, then resolve.' A confirmed
    match (same listingId on both APIs) must auto-resolve and proceed to the
    normal staging path — this is the actual repair for the 491-item find."""
    import tgw.ebay.pull as pull_mod
    monkeypatch.setattr(pull_mod, 'check_legacy_duplicate_listing',
                        lambda cfg, sku, listing_id: {
                            'ok': True, 'match': True, 'duplicate': False,
                            'inventory_listing_id': listing_id, 'inventory_status': 'ACTIVE'})

    _write(tmp_path, 'tgw22', _legacy_item())
    receipt = _run(stage, 'tgw22', force=True, origin='operator')
    assert stage._staged == []
    assert receipt['outcome'] == 'partial'
    assert receipt['evidence']['reason_code'] == 'LEGACY_LISTING_RESOLVED'
    after = json.loads((tmp_path / 'tgw22' / 'tgw22.json').read_text(encoding='utf-8'))
    assert after['legacy_listing_resolved'] is True
    assert after['legacy_listing_blocked']['duplicate_check']['match'] is True
    from tgw.item_mutation import item_generation
    assert receipt['evidence']['resulting_generation'] == item_generation(after)

    # A freshly generated job is now bound to the repaired canonical generation
    # and may proceed through the ordinary force-stage path.
    _run(stage, 'tgw22', force=True, origin='operator')
    assert stage._staged == ['tgw22']


def test_legacy_duplicate_risk_never_resolves(stage, tmp_path, monkeypatch):
    """A mismatch (or no published Inventory offer at all) is exactly the
    genuine-duplicate-listing danger Dave described — must never auto-resolve,
    never touch eBay further, and must be visible in the persisted record."""
    import tgw.ebay.pull as pull_mod
    monkeypatch.setattr(pull_mod, 'check_legacy_duplicate_listing',
                        lambda cfg, sku, listing_id: {
                            'ok': True, 'match': False, 'duplicate': True,
                            'inventory_listing_id': None, 'inventory_status': None,
                            'reason': 'no published Inventory API offer found for this SKU'})

    _write(tmp_path, 'tgw23', _legacy_item())
    _run(stage, 'tgw23', force=True, origin='operator')
    assert stage._staged == []
    after = json.loads((tmp_path / 'tgw23' / 'tgw23.json').read_text(encoding='utf-8'))
    assert 'legacy_listing_resolved' not in after
    assert after['legacy_listing_blocked']['duplicate_check']['duplicate'] is True


def test_legacy_duplicate_check_fetch_error_never_resolves(stage, tmp_path, monkeypatch):
    """A failed live check ('ok': False) must be treated as unresolved, not
    silently treated as safe."""
    import tgw.ebay.pull as pull_mod
    monkeypatch.setattr(pull_mod, 'check_legacy_duplicate_listing',
                        lambda cfg, sku, listing_id: {'ok': False, 'error': 'timeout'})

    _write(tmp_path, 'tgw24', _legacy_item())
    _run(stage, 'tgw24', force=True, origin='operator')
    assert stage._staged == []
    after = json.loads((tmp_path / 'tgw24' / 'tgw24.json').read_text(encoding='utf-8'))
    assert 'legacy_listing_resolved' not in after


def test_stale_offer_price_never_staged(stage, tmp_path):
    """s45 (tgw202605052336026): draft.price is the ONLY price source. A bare
    ebay_offer.price is un-reviewed leftovers from the disabled auto-pricer —
    the old fallback published $40.99 the operator never saw."""
    item = _ready_item()
    item['draft_listing'].pop('price')
    item['ebay_offer']['price'] = 40.99   # stale machine price
    _write(tmp_path, 'tgw8s45', item)
    with pytest.raises(RuntimeError, match='no price'):
        _run(stage, 'tgw8s45')
    assert stage._staged == []


def test_operator_list_without_price_hard_fails_with_finding(stage, tmp_path, monkeypatch):
    """Operator pressed List on an unpriced item: HardFailure (not silent
    retry) + pipeline_error persisted so the editor renders 'needs price'
    (C11)."""
    patched = []
    item = _ready_item()
    item['draft_listing'].pop('price')
    item['ebay_offer']['price'] = 40.99
    path = _write(tmp_path, 'tgw9s45', item)
    from tgw.item_mutation import item_generation
    generation = item_generation(json.loads(path.read_text(encoding='utf-8')))

    def capture_patch(cfg, sku, fields, *, expected_generation=None):
        assert expected_generation == generation
        patched.append((sku, fields))

    monkeypatch.setattr(ebay_stage, 'fence_patch_item', capture_patch)
    with pytest.raises(HardFailure, match='no price set in draft_listing'):
        _run(stage, 'tgw9s45', origin='operator')
    assert stage._staged == []
    assert patched and patched[0][1]['pipeline_error']['code'] == 'no_price_set'


# ---------------------------------------------------------------------------
# Todo #1395 / PP-DEADLETTER-001 — the '99' (Everything Else) fallback
# category is explicitly non-leaf; eBay always rejects staging/publishing
# with it ("The category selected is not a leaf category."). 17 real
# dead-letters (2026-07-05 batch, all confirmed draft_listing.category_id
# == '99') burned a live API call each for a guaranteed HardFailure with no
# actionable trail. Block it locally instead, same shape as the price/title
# guards above.
# ---------------------------------------------------------------------------

def test_fallback_category_99_never_staged_no_api_call(stage, tmp_path, monkeypatch):
    """Reproduces the real dead-letter shape (tgw201501021970513 et al):
    draft_listing.category_id == '99'. Must HardFailure locally — never call
    stage_draft (no wasted/guaranteed-failing eBay API round-trip)."""
    patched = []
    item = _ready_item()
    item['draft_listing']['category_id'] = '99'
    path = _write(tmp_path, 'tgw-cat99', item)
    from tgw.item_mutation import item_generation
    generation = item_generation(json.loads(path.read_text(encoding='utf-8')))

    def capture_patch(cfg, sku, fields, *, expected_generation=None):
        assert expected_generation == generation
        patched.append((sku, fields))

    monkeypatch.setattr(ebay_stage, 'fence_patch_item', capture_patch)
    with pytest.raises(HardFailure, match="fallback '99'"):
        _run(stage, 'tgw-cat99')
    assert stage._staged == []   # stage_draft (the eBay call) never reached
    assert patched and patched[0][1]['pipeline_error']['code'] == 'category_not_leaf'


def test_real_leaf_category_stages_normally(stage, tmp_path):
    """Control: a real category_id (not '99') is unaffected by the new guard."""
    _write(tmp_path, 'tgw-catleaf', _ready_item())  # category_id '12345' in fixture
    _run(stage, 'tgw-catleaf')
    assert stage._staged == ['tgw-catleaf']
