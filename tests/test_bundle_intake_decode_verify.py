"""photo-integrity-mitigation leg 3 (todo #1267,
docs/ai-plans/photo-integrity-mitigation.md): decode-verify at intake.

Live acceptance evidence: a real corrupt (truncated) JPEG placed in a
bundle dir alongside a good one is rejected at the door — never copied
into ItemData — and the rejection is persisted as a durable finding on the
created item record (invariant C11: a guard/skip is a finding, not a log
line), not a silent skip.
"""

import json

import pytest

import tgw.workers.bundle_intake as bundle_intake
from tests.conftest import make_fake_create_item


def _make_worker(tmp_path, newitems_dir, itemdata_root):
    cfg = {
        'newitems_path': newitems_dir,
        'itemdata_root': itemdata_root,
        'pretty': True,
    }
    worker = bundle_intake.BundleIntakeWorker.__new__(bundle_intake.BundleIntakeWorker)
    worker.config = cfg
    return worker


def _real_jpeg_bytes() -> bytes:
    pytest.importorskip("PIL")
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color="green").save(buf, "JPEG")
    return buf.getvalue()


def test_corrupt_photo_rejected_good_photo_kept(tmp_path, monkeypatch):
    newitems_dir = tmp_path / 'newitems'
    itemdata_root = tmp_path / 'ItemData'
    newitems_dir.mkdir()
    itemdata_root.mkdir()

    sku = 'tgw20260101000000001'
    bundle_dir = newitems_dir / sku
    bundle_dir.mkdir()
    (bundle_dir / f'{sku}.json').write_text(
        json.dumps({'location': 'A1', 'title': 'Test Item', 'TEMPLATE': 'default'}),
        encoding='utf-8',
    )

    good_bytes = _real_jpeg_bytes()
    (bundle_dir / 'good.jpg').write_bytes(good_bytes)
    # Real corrupt file: a truncated JPEG (tail cut off) — full im.load()
    # must catch this; header-only im.verify() would not.
    (bundle_dir / 'bad.jpg').write_bytes(good_bytes[: len(good_bytes) // 3])

    monkeypatch.setattr(bundle_intake, 'fence_create_item',
                        make_fake_create_item(itemdata_root))
    monkeypatch.setattr(bundle_intake.state_machine, 'enqueue_catalog_rebuild',
                        lambda *a, **k: None)
    monkeypatch.setattr(bundle_intake.state_machine, 'enqueue_job',
                        lambda *a, **k: 'fake-job-id')

    worker = _make_worker(tmp_path, newitems_dir, itemdata_root)
    worker._handle_dir(sku, bundle_dir)

    dest_dir = itemdata_root / sku
    # Good photo copied; corrupt one never reached ItemData at all.
    assert (dest_dir / 'good.jpg').exists()
    assert not (dest_dir / 'bad.jpg').exists()

    doc = json.loads((dest_dir / f'{sku}.json').read_text(encoding='utf-8'))
    assert doc['pipeline_error']['code'] == 'photo_decode_rejected'
    assert 'bad.jpg' in doc['pipeline_error']['detail']
    assert doc['photo_decode_rejected'][0]['file'] == 'bad.jpg'

    # Source bundle dir fully consumed (not left behind half-processed).
    assert not bundle_dir.exists()


def test_all_photos_corrupt_hard_fails_without_creating_item(tmp_path, monkeypatch):
    newitems_dir = tmp_path / 'newitems'
    itemdata_root = tmp_path / 'ItemData'
    newitems_dir.mkdir()
    itemdata_root.mkdir()

    sku = 'tgw20260101000000002'
    bundle_dir = newitems_dir / sku
    bundle_dir.mkdir()
    (bundle_dir / f'{sku}.json').write_text(
        json.dumps({'location': 'A1', 'title': 'Test Item', 'TEMPLATE': 'default'}),
        encoding='utf-8',
    )
    (bundle_dir / 'bad.jpg').write_bytes(b'not-a-real-jpeg-at-all')

    created = []
    monkeypatch.setattr(
        bundle_intake, 'fence_create_item',
        lambda cfg, sku, data: created.append((sku, data)),
    )

    worker = _make_worker(tmp_path, newitems_dir, itemdata_root)
    with pytest.raises(bundle_intake.HardFailure):
        worker._handle_dir(sku, bundle_dir)

    assert created == []
    assert not (itemdata_root / sku).exists()
