"""Invariants A1/A2/A3/A5/E5 (docs/invariants.md) — item store write rules.

A1: item JSON writes are atomic and leave no temp-file litter.
A2: sku is not bulk-editable (whitelist pin).
A3: create_item never overwrites an existing item.
A5: any field write clears the catalog_verified hall-pass (the verifiedupdate
    bypass was fixed 2026-06-10).
E5: no item JSON is overwritten without archiving the prior state first
    (todo #1104, 2026-07-03/04 — was prose-only, promoted to enforcement).
"""

import json
import os
import zipfile

import pytest

from tgw import items


@pytest.fixture
def cfg(tmp_path):
    return {
        'itemdata_root':      tmp_path / 'ItemData',
        'location_tree_root': tmp_path / 'by-location',
        'archive_root':       tmp_path / 'ItemArchive',
        'pretty':             False,
    }


def _make_item(cfg, sku, **fields):
    return items.create_item(cfg, sku, fields)


# ---------------------------------------------------------------------------
# A1 — atomic writes
# ---------------------------------------------------------------------------

def test_atomic_write_produces_valid_json_and_no_litter(tmp_path):
    path = tmp_path / 'tgw1' / 'tgw1.json'
    items.atomic_write_json(path, {'sku': 'tgw1', 'title': 'thing'})
    assert json.loads(path.read_text(encoding='utf-8'))['title'] == 'thing'
    # temp file must have been renamed away — only the JSON remains
    assert [p.name for p in path.parent.iterdir()] == ['tgw1.json']


def test_atomic_write_overwrite_keeps_single_file(tmp_path):
    path = tmp_path / 'tgw1' / 'tgw1.json'
    items.atomic_write_json(path, {'sku': 'tgw1', 'v': 1})
    items.atomic_write_json(path, {'sku': 'tgw1', 'v': 2})
    assert json.loads(path.read_text(encoding='utf-8'))['v'] == 2
    assert [p.name for p in path.parent.iterdir()] == ['tgw1.json']


def test_atomic_write_new_file_defaults_to_group_writable(tmp_path):
    """NamedTemporaryFile creates its file at 0600 regardless of the parent
    directory's ACL/umask — session 41 confirmed this silently reverts shared
    files (docs/TGW-Plan-Vault) to owner-only on every write. A brand-new file
    must land at 0660, not 0600."""
    path = tmp_path / 'tgw1' / 'tgw1.json'
    items.atomic_write_json(path, {'sku': 'tgw1'})
    assert (path.stat().st_mode & 0o777) == 0o660


def test_atomic_write_preserves_existing_mode(tmp_path):
    """A rewrite must not silently tighten an existing file's permissions —
    match whatever mode was already there rather than always defaulting."""
    path = tmp_path / 'tgw1' / 'tgw1.json'
    items.atomic_write_json(path, {'sku': 'tgw1', 'v': 1})
    os.chmod(path, 0o640)
    items.atomic_write_json(path, {'sku': 'tgw1', 'v': 2})
    assert (path.stat().st_mode & 0o777) == 0o640


# ---------------------------------------------------------------------------
# A3 — creation never overwrites
# ---------------------------------------------------------------------------

def test_create_item_refuses_existing_sku(cfg):
    _make_item(cfg, 'tgw20260101000000000', title='first')
    with pytest.raises(FileExistsError):
        _make_item(cfg, 'tgw20260101000000000', title='second')
    # original record untouched
    doc = json.loads((cfg['itemdata_root'] / 'tgw20260101000000000'
                      / 'tgw20260101000000000.json').read_text())
    assert doc['title'] == 'first'


# ---------------------------------------------------------------------------
# A2 — sku not reachable through bulk edit
# ---------------------------------------------------------------------------

def test_bulk_edit_whitelist_excludes_sku(cfg):
    assert 'sku' not in items.BULK_FIELD_KEYS
    res = items.bulk_edit(cfg, {'sku': 'tgwx'}, 'sku', 'tgwy')
    assert res['ok'] is False


# ---------------------------------------------------------------------------
# A5 — hall-pass invalidation
# ---------------------------------------------------------------------------

def _verified_item(cfg, sku):
    _make_item(cfg, sku, title='t', location='A1')
    items.update_item(cfg, sku, 'catalog_verified',
                      {'ts': '2026-06-01T00:00:00Z', 'by': 'test'})
    return cfg['itemdata_root'] / sku / f'{sku}.json'


def test_field_write_clears_catalog_verified(cfg):
    path = _verified_item(cfg, 'tgw1')
    items.update_item(cfg, 'tgw1', 'title', 'new title')
    assert 'catalog_verified' not in json.loads(path.read_text())


def test_writing_catalog_verified_itself_persists(cfg):
    path = _verified_item(cfg, 'tgw1')
    assert 'catalog_verified' in json.loads(path.read_text())


def test_update_missing_sku_is_clean_error(cfg):
    res = items.update_item(cfg, 'tgw-nope', 'title', 'x')
    assert res['ok'] is False


def test_verifiedupdate_clears_catalog_verified(cfg):
    # verifiedupdate writes via atomic_write_json directly, so it must clear
    # the hall-pass itself (A5 gap fixed 2026-06-10).
    path = _verified_item(cfg, 'tgw1')
    items.verifiedupdate(cfg, 'tgw1', '2026-06-10')
    doc = json.loads(path.read_text())
    assert 'catalog_verified' not in doc
    assert doc['verified'] == '2026-06-10'
    assert doc['#STATUS'] == 'In Stock'


# ---------------------------------------------------------------------------
# PP-FENCE-001 — grep audit: atomic_write_json banned in workers/ and ebay/
# ---------------------------------------------------------------------------
# Known gaps (documented in source with PP-FENCE-001 comment):
#   multi_intake.py:   newitems_dir stub write + key-deletion write (2 sites)
#   ebay_sku_migrate.py: dir-rename + write sequence (3 sites)
#   ebay/pull.py:       restore_archive_tombstone — needs upsert semantics (1 site)
_FENCE_GAPS = frozenset({
    "multi_intake.py",
    "ebay_sku_migrate.py",
    "pull.py",
})


def test_atomic_write_json_banned_in_workers_and_ebay():
    """All workers and ebay/ modules must use fence calls, not atomic_write_json directly."""
    import pathlib
    repo = pathlib.Path(__file__).parents[1]
    targets = [repo / "src" / "tgw" / "workers", repo / "src" / "tgw" / "ebay"]
    violations = []
    for target in targets:
        for py in sorted(target.glob("*.py")):
            if py.name in _FENCE_GAPS:
                continue
            text = py.read_text(encoding="utf-8")
            # Count real atomic_write_json call lines (not comments or import lines)
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.lstrip()
                if "atomic_write_json" in line and not stripped.startswith("#"):
                    # Allow the import in items.py itself; flag usage in workers/ebay
                    if "from tgw.items import" not in line and "import atomic_write_json" not in line:
                        violations.append(f"{py.relative_to(repo)}:{lineno}: {line.strip()}")
    assert not violations, (
        "atomic_write_json call(s) found in workers/ or ebay/ — use fence client instead:\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# E5 — no overwrite without archiving first (todo #1104)
# ---------------------------------------------------------------------------

def test_atomic_write_json_archives_prior_state_on_overwrite(tmp_path):
    path = tmp_path / 'tgw1' / 'tgw1.json'
    archive_root = tmp_path / 'ItemArchive'
    items.atomic_write_json(path, {'sku': 'tgw1', 'v': 1}, archive_root=archive_root)
    # first write is a creation — nothing to archive yet
    assert not (archive_root / 'tgw1.zip').exists()

    items.atomic_write_json(path, {'sku': 'tgw1', 'v': 2}, archive_root=archive_root)
    zpath = archive_root / 'tgw1.zip'
    assert zpath.exists()
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        assert len(names) == 1
        archived = json.loads(zf.read(names[0]))
        assert archived['v'] == 1  # the state BEFORE the second write


def test_atomic_write_json_archives_every_overwrite_as_separate_entry(tmp_path):
    path = tmp_path / 'tgw1' / 'tgw1.json'
    archive_root = tmp_path / 'ItemArchive'
    items.atomic_write_json(path, {'v': 1}, archive_root=archive_root)
    items.atomic_write_json(path, {'v': 2}, archive_root=archive_root)
    items.atomic_write_json(path, {'v': 3}, archive_root=archive_root)
    with zipfile.ZipFile(archive_root / 'tgw1.zip') as zf:
        versions = sorted(json.loads(zf.read(n))['v'] for n in zf.namelist())
        assert versions == [1, 2]  # states before write 2 and write 3


def test_atomic_write_json_without_archive_root_skips_archiving(tmp_path):
    """Backward compatible: callers that don't pass archive_root (non-item
    writers — catalogs, digests, caches) get the old unarchived behavior."""
    path = tmp_path / 'tgw1' / 'tgw1.json'
    items.atomic_write_json(path, {'v': 1})
    items.atomic_write_json(path, {'v': 2})
    assert json.loads(path.read_text())['v'] == 2
    assert not (tmp_path / 'ItemArchive').exists()


def test_write_field_archives_before_overwrite(cfg):
    items.create_item(cfg, 'tgw1', {'title': 'first'})
    items.update_item(cfg, 'tgw1', 'title', 'second')
    zpath = cfg['archive_root'] / 'tgw1.zip'
    assert zpath.exists()
    with zipfile.ZipFile(zpath) as zf:
        archived = json.loads(zf.read(zf.namelist()[0]))
        assert archived['title'] == 'first'


def test_verifiedupdate_archives_before_overwrite(cfg):
    items.create_item(cfg, 'tgw1', {'title': 'thing', 'verified': 'old'})
    items.verifiedupdate(cfg, 'tgw1', '2026-07-04')
    zpath = cfg['archive_root'] / 'tgw1.zip'
    assert zpath.exists()
    with zipfile.ZipFile(zpath) as zf:
        archived = json.loads(zf.read(zf.namelist()[0]))
        assert archived['verified'] == 'old'


def test_archive_failure_aborts_the_write(tmp_path, monkeypatch):
    """E5 is a hard gate, not best-effort: if archiving fails, the overwrite
    must not happen either — silently proceeding would be exactly the
    'delete without archiving' failure mode the invariant exists to prevent."""
    path = tmp_path / 'tgw1' / 'tgw1.json'
    archive_root = tmp_path / 'ItemArchive'
    items.atomic_write_json(path, {'v': 1}, archive_root=archive_root)

    def _boom(*a, **k):
        raise OSError('disk full')
    monkeypatch.setattr(items, '_archive_before_overwrite', _boom)

    with pytest.raises(OSError):
        items.atomic_write_json(path, {'v': 2}, archive_root=archive_root)
    # original content must be untouched — the overwrite never happened
    assert json.loads(path.read_text())['v'] == 1
