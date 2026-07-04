"""PP-PRICING-001 category recompile (todo #1135) — source-loader unit tests."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from recompile_category_backfill import (  # noqa: E402
    _source_historical_master_by_sku_old,
    _source_historical_tgwcatalog,
    _source_searchcatalog_csv,
)


def test_source_historical_tgwcatalog_keys_by_sku(tmp_path):
    (tmp_path / 'historical-tgwcatalog.json').write_text(json.dumps({
        'tgw1': {'eBay category 1 number': '88758', 'eBay category 1 name': 'AC Adapter'},
        'tgw2': {'eBay category 1 number': '', 'eBay category 1 name': ''},
    }))
    result = _source_historical_tgwcatalog(tmp_path)
    assert result == {'tgw1': ('88758', 'AC Adapter')}


def test_source_historical_master_keys_by_sku_old(tmp_path):
    (tmp_path / 'historical-master-catalog.json').write_text(json.dumps([
        {'sku': 'tgw1', 'sku_old': 'TGW1OLD', 'eBay category 1 number': '99',
         'eBay category 1 name': 'Collectibles'},
        {'sku': 'tgw2', 'sku_old': '', 'eBay category 1 number': '100'},
    ]))
    result = _source_historical_master_by_sku_old(tmp_path)
    assert result == {'tgw1old': ('99', 'Collectibles')}


def test_source_searchcatalog_csv_excludes_uncategorized_placeholder(tmp_path):
    csv_path = tmp_path / 'searchcatalog.csv'
    csv_path.write_text('sku,ebaycat\ntgw1,uncategorized\ntgw2,88758\ntgw3,\n')
    result = _source_searchcatalog_csv(tmp_path)
    assert result == {'tgw2': ('88758', '')}
    assert 'tgw1' not in result
    assert 'tgw3' not in result


def test_sources_return_empty_dict_when_file_missing(tmp_path):
    assert _source_historical_tgwcatalog(tmp_path) == {}
    assert _source_historical_master_by_sku_old(tmp_path) == {}
    assert _source_searchcatalog_csv(tmp_path) == {}
