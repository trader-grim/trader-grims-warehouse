"""audit#1143 #1170 — src/tgw/workers/photo_history_recovery.py had no
dry-run safety gate at all, unlike the tools/ near-duplicate it was copied
from (which defaults to dry-run and requires --write). Every found match
was copied straight into live ItemData with no review step.

Fixed to mirror tools/'s convention: ensure_copy()/process_item() take a
write flag (default False); a match is only actually copied when write=True,
otherwise the action is reported as 'would_copy' with nothing touched.
"""

from __future__ import annotations

from tgw.workers import photo_history_recovery as phr


def test_ensure_copy_dry_run_by_default_does_not_touch_disk(tmp_path):
    src = tmp_path / 'src.jpg'
    src.write_bytes(b'photo-bytes')
    dst = tmp_path / 'dest' / 'src.jpg'

    action = phr.ensure_copy(src, dst)  # write defaults to False

    assert action == 'would_copy'
    assert not dst.exists()


def test_ensure_copy_write_true_actually_copies(tmp_path):
    src = tmp_path / 'src.jpg'
    src.write_bytes(b'photo-bytes')
    dst = tmp_path / 'dest' / 'src.jpg'

    action = phr.ensure_copy(src, dst, write=True)

    assert action == 'copied'
    assert dst.read_bytes() == b'photo-bytes'


def test_ensure_copy_existing_dest_reports_exists_without_write_flag(tmp_path):
    src = tmp_path / 'src.jpg'
    src.write_bytes(b'new-bytes')
    dst = tmp_path / 'dest.jpg'
    dst.write_bytes(b'old-bytes')

    action = phr.ensure_copy(src, dst, overwrite=False, write=True)

    assert action == 'exists'
    assert dst.read_bytes() == b'old-bytes'  # untouched


def test_process_item_dry_run_by_default_does_not_copy(tmp_path):
    sku = 'tgw1'
    item_dir = tmp_path / 'ItemData' / sku
    item_dir.mkdir(parents=True)
    item_json = item_dir / f'{sku}.json'
    item_json.write_text('{"photos": ["match.jpg"]}', encoding='utf-8')

    history_dir = tmp_path / 'history'
    history_dir.mkdir()
    (history_dir / 'match.jpg').write_bytes(b'recovered-photo')

    index = phr.build_index([history_dir])
    cfg = {'photo_reference_keys': ['photos'], 'destination': {}}

    rows = phr.process_item(item_json, cfg, index)  # write defaults to False

    assert len(rows) == 1
    assert rows[0]['action'] == 'would_copy'
    assert not (item_dir / 'match.jpg').exists()


def test_process_item_write_true_actually_copies(tmp_path):
    sku = 'tgw2'
    item_dir = tmp_path / 'ItemData' / sku
    item_dir.mkdir(parents=True)
    item_json = item_dir / f'{sku}.json'
    item_json.write_text('{"photos": ["match.jpg"]}', encoding='utf-8')

    history_dir = tmp_path / 'history'
    history_dir.mkdir()
    (history_dir / 'match.jpg').write_bytes(b'recovered-photo')

    index = phr.build_index([history_dir])
    cfg = {'photo_reference_keys': ['photos'], 'destination': {}}

    rows = phr.process_item(item_json, cfg, index, write=True)

    assert rows[0]['action'] == 'copied'
    assert (item_dir / 'match.jpg').read_bytes() == b'recovered-photo'
