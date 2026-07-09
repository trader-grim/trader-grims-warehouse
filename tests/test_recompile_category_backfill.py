"""PP-PRICING-001 category recompile (todo #1135) — source-loader unit tests."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from recompile_category_backfill import (  # noqa: E402
    _canonical_category,
    _legacy_category,
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


class TestCanonicalVsLegacyCategory:
    """audit#1143 #1209: _canonical_category() must NOT fall back to the
    legacy raw field the way velocity._category() does — that fallback is
    exactly what let the backfill treat a legacy-only item as "already
    has a category" and skip promoting it, so a later
    data_scrub_legacy_ebay_fields.py run could delete the item's only
    category signal."""

    def test_canonical_prefers_draft_listing(self):
        doc = {'draft_listing': {'category_id': '111', 'category_name': 'A'},
               'ebay_category_id': '222', 'eBay category 1 number': '333'}
        assert _canonical_category(doc) == ('111', 'A')

    def test_canonical_falls_back_to_ebay_category_id(self):
        doc = {'ebay_category_id': '222', 'ebay_category_name': 'B',
               'eBay category 1 number': '333'}
        assert _canonical_category(doc) == ('222', 'B')

    def test_canonical_does_not_fall_back_to_legacy_raw_field(self):
        doc = {'eBay category 1 number': '333', 'eBay category 1 name': 'C'}
        assert _canonical_category(doc) == ('', '')

    def test_canonical_empty_when_nothing_set(self):
        assert _canonical_category({}) == ('', '')

    def test_legacy_category_reads_raw_field(self):
        doc = {'eBay category 1 number': '333', 'eBay category 1 name': 'C'}
        assert _legacy_category(doc) == ('333', 'C')

    def test_legacy_category_empty_when_absent(self):
        assert _legacy_category({}) == ('', '')


class TestPromotionSurvivesLegacyScrub:
    """End-to-end reproduction of the audit#1143 #1209 scenario: an item
    whose only category signal is the legacy raw field must still have a
    category after (1) the recompile promotion step and (2) a
    data_scrub_legacy_ebay_fields.py-style strip of the now-redundant
    legacy field — in that order, which is the order the real scripts ran
    in production (scrub 2026-07-03, recompile 2026-07-04)."""

    def test_promote_then_scrub_keeps_canonical_category(self, tmp_path):
        import data_scrub_legacy_ebay_fields as scrub_mod

        itemdata_root = tmp_path / 'ItemData'
        archive_root = tmp_path / 'ItemArchive'
        sku = 'tgw20260101000000001'
        item_dir = itemdata_root / sku
        item_dir.mkdir(parents=True)
        doc = {'sku': sku, 'eBay category 1 number': '7317',
               'eBay category 1 name': 'Game Pieces, Parts'}
        (item_dir / f'{sku}.json').write_text(json.dumps(doc), encoding='utf-8')
        cfg = {'itemdata_root': itemdata_root, 'archive_root': archive_root, 'pretty': True}

        # Step 1: recompile's per-item decision — legacy-only, must promote.
        canonical_id, _ = _canonical_category(doc)
        assert canonical_id == ''  # confirms this item hits the promotion branch
        legacy_id, legacy_name = _legacy_category(doc)
        assert legacy_id == '7317'

        from tgw import items
        result = items.set_fields(cfg, sku, {
            'ebay_category_id': legacy_id, 'ebay_category_name': legacy_name,
        })
        assert result['ok'] and result['set']

        # Step 2: scrub script's field-safety check — the raw field matches
        # "history" (simulated here as the value itself, since the scrub's
        # real match target is the historical catalog and that's not the
        # thing under test), AND the item is now promoted, so it's safe.
        current_doc = json.loads((item_dir / f'{sku}.json').read_text())
        hist_record = {'eBay category 1 number': '7317'}
        safe, exceptions, _, held = scrub_mod._scan_item(sku, current_doc, hist_record)
        assert 'eBay category 1 number' in safe
        assert exceptions == []
        assert held == []

        strip_result = items.strip_fields(cfg, sku, safe)
        assert strip_result['ok']

        # The canonical field must still carry the category after both
        # steps — this is exactly what was silently lost before #1209.
        final_doc = json.loads((item_dir / f'{sku}.json').read_text())
        assert 'eBay category 1 number' not in final_doc
        assert final_doc['ebay_category_id'] == '7317'
        assert final_doc['ebay_category_name'] == 'Game Pieces, Parts'
        final_cat_id, _ = _canonical_category(final_doc)
        assert final_cat_id == '7317'

    def test_scrub_holds_legacy_category_field_when_not_yet_promoted(self):
        # audit#1143 #1252: the scrub script's own deletion-site guard —
        # even if recompile_category_backfill.py was never run (or is run
        # out of order), the scrub must not delete the item's only category
        # signal. This is what #1209's fix alone did NOT prevent.
        import data_scrub_legacy_ebay_fields as scrub_mod

        doc = {'sku': 'tgw1', 'eBay category 1 number': '7317',
               'eBay category 1 name': 'Game Pieces, Parts'}
        hist_record = {'eBay category 1 number': '7317', 'eBay category 1 name': 'Game Pieces, Parts'}

        safe, exceptions, no_history, held = scrub_mod._scan_item('tgw1', doc, hist_record)

        assert 'eBay category 1 number' not in safe
        assert 'eBay category 1 name' not in safe
        assert set(held) == {'eBay category 1 number', 'eBay category 1 name'}
        assert exceptions == []

    def test_scrub_removes_legacy_category_field_once_canonical_present(self):
        import data_scrub_legacy_ebay_fields as scrub_mod

        doc = {'sku': 'tgw1', 'ebay_category_id': '7317',
               'eBay category 1 number': '7317', 'eBay category 1 name': 'Game Pieces, Parts'}
        hist_record = {'eBay category 1 number': '7317', 'eBay category 1 name': 'Game Pieces, Parts'}

        safe, exceptions, no_history, held = scrub_mod._scan_item('tgw1', doc, hist_record)

        assert set(safe) == {'eBay category 1 number', 'eBay category 1 name'}
        assert held == []
