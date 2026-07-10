"""Tests for the raw eBay response capture layer (PRIME DIRECTIVE 1, session 42).

Every response eBay sends is preserved at the client fence before any worker
touches it — no worker can forget preservation because it isn't the worker's
job. Capture must be fail-open: a capture error never breaks the API call.
"""

from __future__ import annotations

import gzip
import json

from tgw.apis.ebay.client import capture_response


def _cfg(tmp_path, **raw):
    return {'raw': {'ebay_capture_root': str(tmp_path / 'cap'), **raw}}


def _read_all(tmp_path):
    files = sorted((tmp_path / 'cap').glob('*.jsonl.gz'))
    assert files, 'no capture file written'
    recs = []
    for f in files:
        text = gzip.decompress(f.read_bytes()).decode('utf-8')
        recs.extend(json.loads(line) for line in text.splitlines() if line)
    return recs


class TestCapture:
    def test_response_body_preserved(self, tmp_path):
        cfg = _cfg(tmp_path)
        capture_response(cfg, 'rest', 'GET /sell/inventory/v1/offer',
                         {'sku': 'tgw123'}, 200, b'{"offers": []}')
        rec = _read_all(tmp_path)[-1]
        assert rec['api'] == 'rest'
        assert rec['name'] == 'GET /sell/inventory/v1/offer'
        assert rec['params'] == {'sku': 'tgw123'}
        assert rec['status'] == 200
        assert json.loads(rec['body']) == {'offers': []}
        assert rec['ts'].endswith('+00:00')  # invariant E6: stored tz-aware UTC

    def test_error_responses_also_preserved(self, tmp_path):
        # A 429/500 body is still data eBay sent us — preserved like any other.
        cfg = _cfg(tmp_path)
        capture_response(cfg, 'rest', 'GET /x', None, 429, b'{"errors": [1]}')
        rec = _read_all(tmp_path)[-1]
        assert rec['status'] == 429
        assert 'errors' in rec['body']

    def test_concurrent_style_appends_all_readable(self, tmp_path):
        # Multiple gzip members appended to one file must all decompress.
        cfg = _cfg(tmp_path)
        for i in range(5):
            capture_response(cfg, 'trading', f'Call{i}', None, 200, b'<xml/>')
        recs = _read_all(tmp_path)
        assert len(recs) == 5
        assert {r['name'] for r in recs} == {f'Call{i}' for i in range(5)}

    def test_oversize_body_recorded_as_metadata(self, tmp_path):
        cfg = _cfg(tmp_path)
        big = b'x' * (6 * 1024 * 1024)
        capture_response(cfg, 'rest', 'GET /bulk', None, 200, big)
        rec = _read_all(tmp_path)[-1]
        assert 'body' not in rec
        assert rec['bytes'] == len(big)
        assert 'body_omitted' in rec

    def test_disabled_by_config(self, tmp_path):
        cfg = _cfg(tmp_path, ebay_capture_enabled=False)
        capture_response(cfg, 'rest', 'GET /x', None, 200, b'{}')
        assert not (tmp_path / 'cap').exists()

    def test_fail_open_on_unwritable_root(self):
        cfg = {'raw': {'ebay_capture_root': '/nonexistent-root/cap'}}
        capture_response(cfg, 'rest', 'GET /x', None, 200, b'{}')  # must not raise
