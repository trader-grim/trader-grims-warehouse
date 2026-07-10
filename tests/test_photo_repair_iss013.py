"""audit#1143 #1211 — photo_repair_iss013.py's wrong-primary cleanup step.

The cleanup that removes a wrongly-created <SKU>.jpg (leftover from a prior
repair attempt) used to unlink it based on byte-size match alone, with no
content-hash check and no archive-before-delete. A coincidental same-size,
different-content file would have been permanently destroyed (violates E5).
Fixed to require a content-hash match (mirroring the alt-rename's own
content check) and to archive to history/ before deleting (mirroring the
alt-rename's copy2-to-history step), rather than a bare unlink.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import photo_repair_iss013 as pr  # noqa: E402

JPEG_MAGIC = b'\xff\xd8\xff'


def _make_item(tmp_path, sku, original_content, wrong_content=None, wrong_size_override=None):
    item_dir = tmp_path / 'ItemData' / sku
    item_dir.mkdir(parents=True)
    original = item_dir / f'{sku[:3]}20260101_120000.jpg'
    original.write_bytes(original_content)
    (item_dir / f'{sku}-alt.jpg').write_bytes(original_content)
    if wrong_content is not None:
        wrong = item_dir / f'{sku}.jpg'
        if wrong_size_override is not None:
            wrong.write_bytes(wrong_content.ljust(wrong_size_override, b'\x00')[:wrong_size_override])
        else:
            wrong.write_bytes(wrong_content)
    return item_dir


def _setup_roots(tmp_path, monkeypatch):
    itemdata_root = tmp_path / 'ItemData'
    history_root = tmp_path / 'history' / 'ItemData'
    monkeypatch.setattr(pr, 'ITEMDATA_ROOT', itemdata_root)
    monkeypatch.setattr(pr, 'HISTORY_ROOT', history_root)
    return itemdata_root, history_root


class TestWrongPrimaryCleanup:
    def test_removes_wrong_primary_only_when_content_matches_and_archives_first(self, tmp_path, monkeypatch):
        sku = 'tgw202601010001'
        itemdata_root, history_root = _setup_roots(tmp_path, monkeypatch)
        content = JPEG_MAGIC + b'X' * 200
        _make_item(tmp_path, sku, content, wrong_content=content)

        r = pr.repair_item(sku, execute=True)

        assert r['status'] == 'RENAMED'
        wrong_primary = itemdata_root / sku / f'{sku}.jpg'
        assert not wrong_primary.exists()
        archived = history_root / sku / f'{sku}.jpg'
        assert archived.exists()
        assert archived.read_bytes() == content

    def test_leaves_wrong_primary_when_same_size_but_different_content(self, tmp_path, monkeypatch):
        sku = 'tgw202601010002'
        itemdata_root, history_root = _setup_roots(tmp_path, monkeypatch)
        original_content = JPEG_MAGIC + b'A' * 200
        # Same size as original, but different bytes — must NOT be deleted.
        decoy_content = JPEG_MAGIC + b'B' * 200
        _make_item(tmp_path, sku, original_content, wrong_content=decoy_content)

        r = pr.repair_item(sku, execute=True)

        assert r['status'] == 'RENAMED'
        wrong_primary = itemdata_root / sku / f'{sku}.jpg'
        assert wrong_primary.exists()
        assert wrong_primary.read_bytes() == decoy_content
        assert not (history_root / sku / f'{sku}.jpg').exists()

    def test_leaves_wrong_primary_when_size_differs(self, tmp_path, monkeypatch):
        sku = 'tgw202601010003'
        itemdata_root, _ = _setup_roots(tmp_path, monkeypatch)
        original_content = JPEG_MAGIC + b'A' * 200
        smaller_content = JPEG_MAGIC + b'C' * 50
        _make_item(tmp_path, sku, original_content, wrong_content=smaller_content)

        r = pr.repair_item(sku, execute=True)

        assert r['status'] == 'RENAMED'
        wrong_primary = itemdata_root / sku / f'{sku}.jpg'
        assert wrong_primary.exists()

    def test_no_wrong_primary_present_is_a_noop(self, tmp_path, monkeypatch):
        sku = 'tgw202601010004'
        _setup_roots(tmp_path, monkeypatch)
        content = JPEG_MAGIC + b'A' * 200
        _make_item(tmp_path, sku, content)

        r = pr.repair_item(sku, execute=True)

        assert r['status'] == 'RENAMED'
