"""audit#1143 / todo #1207 — ebay_normalize.py never set the background
quota context before its fence_patch_item calls, so http_server treated
every write as operator-originated and auto-enqueued a live force=True
ebay_stage push to eBay for ~19k items despite the script's own docstring
promising "No eBay API calls".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import ebay_normalize  # noqa: E402

from tgw import quota  # noqa: E402


def test_main_sets_background_quota_context_before_writes(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ebay_normalize.quota, 'set_context',
                         lambda kind, name: calls.append((kind, name)))

    monkeypatch.setattr(ebay_normalize, 'load_config', lambda path: {
        'secrets_root': tmp_path, 'raw': {},
    })
    (tmp_path / 'tgw-api-key.json').write_text('{"api_key": "x"}')
    monkeypatch.setattr(ebay_normalize, 'iter_all_skus', lambda cfg: [])
    monkeypatch.setattr(sys, 'argv', ['ebay_normalize.py', '--dry-run'])

    ebay_normalize.main()

    assert calls, 'quota.set_context was never called'
    assert calls[0] == ('background', 'ebay_normalize')


def test_context_set_before_iter_all_skus_is_called(monkeypatch, tmp_path):
    """The context must be live before any fence write, not merely called
    somewhere — assert ordering by having iter_all_skus check current context."""
    monkeypatch.setattr(ebay_normalize, 'load_config', lambda path: {
        'secrets_root': tmp_path, 'raw': {},
    })
    (tmp_path / 'tgw-api-key.json').write_text('{"api_key": "x"}')

    seen_context = {}

    def fake_iter_all_skus(cfg):
        seen_context['kind'] = quota.context_kind()
        return []

    monkeypatch.setattr(ebay_normalize, 'iter_all_skus', fake_iter_all_skus)
    monkeypatch.setattr(sys, 'argv', ['ebay_normalize.py', '--dry-run'])

    ebay_normalize.main()

    assert seen_context['kind'] == 'background'
