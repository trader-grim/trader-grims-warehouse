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

    worker = _make_worker(tmp_path, newitems_dir, itemdata_root)
    children = worker._extract_items(zip_path, newitems_dir, base_sku, 'LOC1', 'default')

    assert children == [base_sku]
    # The existing record must be completely untouched — no strip, no archive write.
    assert json.loads(existing_path.read_text(encoding='utf-8')) == original_doc
    # A collision must be surfaced, not silent.
    assert len(notified) == 1
    assert 'collision' in notified[0][0][0].lower()

    # The normal newitems_dir path still gets the stub + photo, as usual.
    stub_path = newitems_dir / base_sku / f'{base_sku}.json'
    assert stub_path.exists()


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
