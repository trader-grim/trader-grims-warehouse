"""Data Charter observability (todo #1103) — dataset-growth lines in ops-digest.

Covers _dataset_growth() (eBayCapture bytes + ItemArchive coverage, cheap
stat/listdir only) and render_text's rendering + the "pipeline ran but
dataset didn't grow" stall flag, computed in collect().
"""

import gzip
from datetime import datetime, timezone

from tgw.ops_digest import _dataset_growth, render_text


def _cfg(tmp_path, capture_root=None, itemdata_root=None, archive_root=None):
    return {
        'raw': {'ebay_capture_root': str(capture_root)} if capture_root else {},
        'itemdata_root': itemdata_root,
        'archive_root': archive_root,
    }


def test_dataset_growth_missing_capture_file_is_zero_bytes(tmp_path):
    result = _dataset_growth(_cfg(tmp_path, capture_root=tmp_path / 'nope'))
    assert result['capture_bytes_today'] == 0


def test_dataset_growth_reads_todays_capture_file_size(tmp_path):
    capture_root = tmp_path / 'ebay'
    capture_root.mkdir()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    f = capture_root / f'{today}.jsonl.gz'
    f.write_bytes(gzip.compress(b'{"a": 1}\n' * 100))
    result = _dataset_growth(_cfg(tmp_path, capture_root=capture_root))
    assert result['capture_bytes_today'] == f.stat().st_size
    assert result['capture_bytes_today'] > 0


def test_dataset_growth_archive_coverage_fraction(tmp_path):
    itemdata_root = tmp_path / 'ItemData'
    archive_root = tmp_path / 'ItemArchive'
    itemdata_root.mkdir()
    archive_root.mkdir()
    for sku in ('tgw1', 'tgw2', 'tgw3', 'tgw4'):
        (itemdata_root / sku).mkdir()
    (archive_root / 'tgw1.zip').write_bytes(b'')
    (archive_root / 'tgw2.zip').write_bytes(b'')

    result = _dataset_growth(_cfg(tmp_path, itemdata_root=itemdata_root, archive_root=archive_root))
    assert result['total_items'] == 4
    assert result['archived_items'] == 2
    assert result['archive_fraction'] == 0.5


def test_dataset_growth_no_items_gives_none_fraction(tmp_path):
    itemdata_root = tmp_path / 'ItemData'
    itemdata_root.mkdir()
    result = _dataset_growth(_cfg(tmp_path, itemdata_root=itemdata_root))
    assert result['total_items'] == 0
    assert result['archive_fraction'] is None


def _base_digest(**overrides):
    d = {
        'generated_at': '2026-07-04T05:00:00+00:00',
        'previous_run': None,
        'health_ok': True,
        'checks_flagged': [],
        'queues': {'queued': 0, 'processing': 0},
        'dead_letters': {},
        'dead_letter_delta': {},
        'restarts': {},
        'restart_flags': {},
        'quota': {'incidents_today': 0, 'pools': {}},
        'oldest_inbox_note': None,
        'catalog_verify': None,
        'retry_wait': [],
        'morning_exposure': [],
        'dataset_growth': None,
    }
    d.update(overrides)
    return d


def test_render_omits_dataset_growth_when_absent():
    text = render_text(_base_digest())
    assert 'DATASET GROWTH' not in text


def test_render_shows_dataset_growth_clean():
    d = _base_digest(dataset_growth={
        'capture_bytes_today': 12345, 'capture_bytes_delta': 500,
        'total_items': 100, 'archived_items': 40, 'archive_fraction': 0.4,
        'capture_stalled': False, 'date': '2026-07-04',
    })
    text = render_text(d)
    assert 'DATASET GROWTH — eBayCapture today: 12,345 bytes (+500 bytes since last look)' in text
    assert 'ItemArchive coverage: 40/100 items (40%)' in text
    assert 'RED DATASET GROWTH' not in text


def test_render_flags_stalled_capture_red():
    d = _base_digest(dataset_growth={
        'capture_bytes_today': 500, 'capture_bytes_delta': 0,
        'total_items': 10, 'archived_items': 0, 'archive_fraction': 0.0,
        'capture_stalled': True, 'date': '2026-07-04',
    })
    text = render_text(d)
    assert 'RED DATASET GROWTH' in text
    assert 'something is discarding raw responses again' in text
