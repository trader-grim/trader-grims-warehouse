"""audit#1143 follow-up (session 48) — multi_intake.py used to directly patch
an existing ItemData record when a derived child SKU collided with one
(stripping 'Item number' with no live-listing check, no archive, bypassing
the fence). One production case (tgw202604130911246) turned out to be a
currently-Active eBay listing with no corroborating siblings — the branch
was never actually verified safe. It's been removed: bundle_intake's own
idempotent no-op-on-existing-SKU handling already covers the collision
safely, so multi_intake now only logs/notifies and leaves the existing
record untouched.
"""

import json
import zipfile
from pathlib import Path

import tgw.workers.multi_intake as multi_intake
from tgw import api as tgw_api


def _make_worker(tmp_path, newitems_dir, itemdata_root):
    cfg = {
        'newitems_path':  newitems_dir,
        'itemdata_root':  itemdata_root,
        'pretty':         True,
    }
    worker = multi_intake.MultiIntakeWorker.__new__(multi_intake.MultiIntakeWorker)
    worker.config = cfg
    return worker


def _make_zip(zip_path: Path, ts_dirs_with_images: dict):
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for ts_dir, image_names in ts_dirs_with_images.items():
            for name in image_names:
                zf.writestr(f'{ts_dir}/{name}', b'fake-jpeg-bytes')


def test_extract_items_skips_existing_sku_without_touching_it(tmp_path, monkeypatch):
    newitems_dir = tmp_path / 'newitems'
    itemdata_root = tmp_path / 'ItemData'
    newitems_dir.mkdir()
    itemdata_root.mkdir()

    base_sku = 'tgw20260101000000010'
    zip_path = tmp_path / 'data.zip'
    _make_zip(zip_path, {'20260101000000': ['1.jpg']})

    # Pre-existing ItemData record at the derived child SKU (collision).
    existing_dir = itemdata_root / base_sku
    existing_dir.mkdir()
    existing_path = existing_dir / f'{base_sku}.json'
    original_doc = {'sku': base_sku, 'title': 'Real Item', 'Item number': '999888777'}
    existing_path.write_text(json.dumps(original_doc), encoding='utf-8')

    notified = []
    import tgw.notify as notify_mod
    monkeypatch.setattr(notify_mod, 'notify', lambda *a, **k: notified.append((a, k)))
    # Isolate the collision-notify dedup registry from the real production
    # path (audit#1143 #1246) — must not read/write /opt/TGW/var for a test.
    monkeypatch.setattr(multi_intake, '_COLLISION_NOTIFY_REGISTRY', tmp_path / 'collision-notified.json')

    from tests.conftest import make_fake_patch_item
    monkeypatch.setattr(multi_intake, 'fence_patch_item', make_fake_patch_item(itemdata_root))

    worker = _make_worker(tmp_path, newitems_dir, itemdata_root)
    children = worker._extract_items(zip_path, newitems_dir, base_sku, 'LOC1', 'default')

    assert children == [base_sku]
    # The existing record's original fields must be untouched — no strip, no
    # archive write — but todo #1304 (invariant C11) requires a durable
    # `sku_collision_blocked` finding to be added additively.
    updated_doc = json.loads(existing_path.read_text(encoding='utf-8'))
    for key, val in original_doc.items():
        assert updated_doc[key] == val
    collision = updated_doc['sku_collision_blocked']
    assert collision['colliding_sku'] == base_sku
    assert collision['base_sku'] == base_sku
    assert collision['detected_at']

    # catalog-verify must surface this as an unrepaired finding.
    viols = tgw_api._verify_item(base_sku, existing_dir, updated_doc)
    rules = [v_['rule'] for v_ in viols]
    assert 'sku_collision_unrepaired' in rules

    # A collision must be surfaced, not silent.
    assert len(notified) == 1
    assert 'collision' in notified[0][0][0].lower()

    # The normal newitems_dir path still gets the stub + photo, as usual.
    stub_path = newitems_dir / base_sku / f'{base_sku}.json'
    assert stub_path.exists()

    # audit#1143 #1246: notify text must give the operator an actionable
    # next step, not just "verify it's not a duplicate".
    message = notified[0][0][1]
    assert 'ebay_stage' in message
    assert 'duplicate-check' in message


def test_collision_notify_is_deduped_across_batch_redrop(tmp_path, monkeypatch):
    # Regression for #1246: _child_skus() is deterministic, so re-dropping
    # the identical zip reproduces the exact same collision on the exact
    # same SKU every time — the external notify() channel must not be
    # spammed once per re-drop.
    newitems_dir = tmp_path / 'newitems'
    itemdata_root = tmp_path / 'ItemData'
    newitems_dir.mkdir()
    itemdata_root.mkdir()

    base_sku = 'tgw20260101000000030'
    zip_path = tmp_path / 'data.zip'
    _make_zip(zip_path, {'20260101000000': ['1.jpg']})

    existing_dir = itemdata_root / base_sku
    existing_dir.mkdir()
    (existing_dir / f'{base_sku}.json').write_text(
        json.dumps({'sku': base_sku, 'title': 'Real Item'}), encoding='utf-8')

    notified = []
    import tgw.notify as notify_mod
    monkeypatch.setattr(notify_mod, 'notify', lambda *a, **k: notified.append((a, k)))
    registry_path = tmp_path / 'collision-notified.json'
    monkeypatch.setattr(multi_intake, '_COLLISION_NOTIFY_REGISTRY', registry_path)

    from tests.conftest import make_fake_patch_item
    monkeypatch.setattr(multi_intake, 'fence_patch_item', make_fake_patch_item(itemdata_root))

    worker = _make_worker(tmp_path, newitems_dir, itemdata_root)

    # First run: notify fires once, registry is written.
    worker._extract_items(zip_path, newitems_dir, base_sku, 'LOC1', 'default')
    assert len(notified) == 1
    assert registry_path.exists()

    # Simulate the batch being re-dropped: same zip, same base_sku, fresh
    # newitems_dir target (as multi_intake's own re-run would do).
    worker._extract_items(zip_path, newitems_dir, base_sku, 'LOC1', 'default')

    # notify() must NOT have been called again — still just the one call.
    assert len(notified) == 1


def test_extract_items_no_notify_when_sku_is_fresh(tmp_path, monkeypatch):
    newitems_dir = tmp_path / 'newitems'
    itemdata_root = tmp_path / 'ItemData'
    newitems_dir.mkdir()
    itemdata_root.mkdir()

    base_sku = 'tgw20260101000000020'
    zip_path = tmp_path / 'data.zip'
    _make_zip(zip_path, {'20260101000000': ['1.jpg']})

    notified = []
    import tgw.notify as notify_mod
    monkeypatch.setattr(notify_mod, 'notify', lambda *a, **k: notified.append((a, k)))

    worker = _make_worker(tmp_path, newitems_dir, itemdata_root)
    children = worker._extract_items(zip_path, newitems_dir, base_sku, 'LOC1', 'default')

    assert children == [base_sku]
    assert notified == []
    assert not (itemdata_root / base_sku).exists()
