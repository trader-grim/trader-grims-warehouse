"""todo #1383 / audit#COHESION-2026-07 — tools/photo_history_recovery.py's
recover_item() previously wrote straight to the live dest path with
shutil.copy2(src, dest). Fixed to mirror #1307's worker-side fix: copy to
a temp file in the same destination directory then os.replace() onto the
final path, so a reader never observes a partial/corrupt photo and no
stray "tmp*" file survives a crash mid-copy.

Mirrors tests/test_photo_history_recovery_dry_run.py's ensure_copy()
atomic-copy tests (test_ensure_copy_leaves_no_tmp_file_behind_on_success /
test_ensure_copy_uses_temp_file_then_atomic_replace /
test_ensure_copy_cleans_up_temp_file_on_copy_failure) for the tools/
sibling's recover_item(), which has its own independent implementation.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from tools import photo_history_recovery as phr_tools


def test_recover_item_leaves_no_tmp_file_behind_on_success(tmp_path):
    item_dir = tmp_path / 'ItemData' / 'tgwTEST1'
    item_dir.mkdir(parents=True)
    doc = {'photos': ['photo.jpg']}

    history_dir = tmp_path / 'history'
    history_dir.mkdir()
    src = history_dir / 'photo.jpg'
    src.write_bytes(b'photo-bytes')
    index = phr_tools.build_photo_index([history_dir])

    rows = phr_tools.recover_item(item_dir, doc, ['photos'], index,
                                   overwrite=False, write=True, verbose=False)

    assert rows[0]['action'] == 'copied'
    dest = item_dir / 'photo.jpg'
    assert dest.read_bytes() == b'photo-bytes'
    leftover = [p for p in item_dir.iterdir() if p != dest]
    assert leftover == []


def test_recover_item_uses_temp_file_then_atomic_replace(tmp_path):
    item_dir = tmp_path / 'ItemData' / 'tgwTEST2'
    item_dir.mkdir(parents=True)
    doc = {'photos': ['photo.jpg']}

    history_dir = tmp_path / 'history'
    history_dir.mkdir()
    src = history_dir / 'photo.jpg'
    src.write_bytes(b'photo-bytes')
    index = phr_tools.build_photo_index([history_dir])

    real_replace = phr_tools.os.replace
    calls = []

    def spy_replace(a, b):
        # at the moment of replace, the temp file must already contain the
        # full bytes and the final destination must not exist yet — proves
        # the write happened to a side path, not in-place on dest.
        calls.append((str(a), str(b)))
        assert Path(a).read_bytes() == b'photo-bytes'
        assert not Path(b).exists()
        return real_replace(a, b)

    with mock.patch.object(phr_tools.os, 'replace', side_effect=spy_replace):
        rows = phr_tools.recover_item(item_dir, doc, ['photos'], index,
                                       overwrite=False, write=True, verbose=False)

    dest = item_dir / 'photo.jpg'
    assert rows[0]['action'] == 'copied'
    assert len(calls) == 1
    tmp_name = Path(calls[0][0]).name
    assert tmp_name != dest.name
    assert tmp_name.startswith(dest.name)
    assert dest.read_bytes() == b'photo-bytes'


def test_recover_item_cleans_up_temp_file_on_copy_failure(tmp_path):
    item_dir = tmp_path / 'ItemData' / 'tgwTEST3'
    item_dir.mkdir(parents=True)
    doc = {'photos': ['photo.jpg']}

    history_dir = tmp_path / 'history'
    history_dir.mkdir()
    src = history_dir / 'photo.jpg'
    src.write_bytes(b'photo-bytes')
    index = phr_tools.build_photo_index([history_dir])

    with mock.patch.object(phr_tools.shutil, 'copy2', side_effect=OSError('disk full')):
        rows = phr_tools.recover_item(item_dir, doc, ['photos'], index,
                                       overwrite=False, write=True, verbose=False)

    dest = item_dir / 'photo.jpg'
    assert rows[0]['action'] == 'error'
    assert rows[0]['error'] == 'disk full'
    assert not dest.exists()
    assert list(item_dir.iterdir()) == []
