"""audit#1143 #1239 (merged #1179+#1180) — shared locking helper for eBay
API disk caches.

specifics.py's get_aspects() cache and taxonomy.py's category-tree caches
both did unlocked, non-atomic disk read-modify-write: two concurrent
cache-miss writers could race and silently drop each other's new entries,
and a crash mid-write could corrupt the whole cache file.

Code-review follow-up: the module's own hand-rolled atomic write was
replaced with a delegation to tgw.catalog.atomic_write_json() — the
existing, already-tested helper for this class of file — after the
hand-rolled version was found to silently narrow cache file permissions
(NamedTemporaryFile always creates at 0600; catalog.atomic_write_json
preserves the target's existing mode). See test_catalog_atomic_write_perms.py
for that helper's own mode-preservation coverage; this file only covers
locked_merge_cache_json's own locking/merge behavior now.
"""

from __future__ import annotations

import json
import os

from tgw.apis.ebay._cache_io import locked_merge_cache_json


class TestLockedMergeCacheJson:
    def test_creates_file_from_empty_when_absent(self, tmp_path):
        path = tmp_path / 'cache.json'
        result = locked_merge_cache_json(path, lambda cur: {**cur, 'cat1': 'a'})
        assert result == {'cat1': 'a'}
        assert json.loads(path.read_text()) == {'cat1': 'a'}

    def test_merges_into_existing_entries_without_dropping_them(self, tmp_path):
        path = tmp_path / 'cache.json'
        path.write_text(json.dumps({'cat1': 'a'}), encoding='utf-8')

        result = locked_merge_cache_json(path, lambda cur: {**cur, 'cat2': 'b'})

        assert result == {'cat1': 'a', 'cat2': 'b'}
        assert json.loads(path.read_text()) == {'cat1': 'a', 'cat2': 'b'}

    def test_sequential_writers_each_see_the_others_entry(self, tmp_path):
        # Regression for #1239: this is exactly the race the old code had —
        # writer A reads {}, writer B reads {}, A writes {catA}, B writes
        # {catB} and silently drops A's entry. locked_merge_cache_json reads
        # FRESH state under the lock on every call, so sequential writers
        # (the realistic case — writes are fast, held only for the merge)
        # never clobber each other.
        path = tmp_path / 'cache.json'
        locked_merge_cache_json(path, lambda cur: {**cur, 'catA': 'valueA'})
        locked_merge_cache_json(path, lambda cur: {**cur, 'catB': 'valueB'})

        assert json.loads(path.read_text()) == {'catA': 'valueA', 'catB': 'valueB'}

    def test_recovers_from_corrupt_existing_file_instead_of_crashing(self, tmp_path):
        path = tmp_path / 'cache.json'
        path.write_text('{not valid json', encoding='utf-8')

        result = locked_merge_cache_json(path, lambda cur: {**cur, 'cat1': 'a'})

        assert result == {'cat1': 'a'}

    def test_lock_file_created_alongside_cache(self, tmp_path):
        path = tmp_path / 'cache.json'
        locked_merge_cache_json(path, lambda cur: {**cur, 'cat1': 'a'})
        assert (tmp_path / 'cache.json.lock').exists()

    def test_preserves_existing_file_mode_through_merge(self, tmp_path):
        # Regression for #1239's permission-mode fix: merging must go
        # through catalog.atomic_write_json's mode-preserving write, not a
        # bare NamedTemporaryFile rename that silently narrows to 0600.
        path = tmp_path / 'cache.json'
        path.write_text(json.dumps({'cat1': 'a'}), encoding='utf-8')
        os.chmod(path, 0o644)

        locked_merge_cache_json(path, lambda cur: {**cur, 'cat2': 'b'})

        assert (path.stat().st_mode & 0o777) == 0o644

    def test_no_leftover_tmp_files_after_write(self, tmp_path):
        path = tmp_path / 'cache.json'
        locked_merge_cache_json(path, lambda cur: {**cur, 'cat1': 'a'})
        leftovers = [p for p in tmp_path.iterdir() if p.name not in (path.name, path.name + '.lock')]
        assert leftovers == []
