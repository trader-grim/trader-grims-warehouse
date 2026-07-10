"""audit#1143 / todo #1235 (merged #1162+#1163+#1164+#1177+#1208+#1212) —
non-JSON durable docs (Master Plan) had no atomic write + no
archive-before-overwrite. atomic_write_text() gives them the same
tmp+rename+archive guarantee as atomic_write_json()."""

import json
import tempfile
import zipfile
from pathlib import Path

from tgw.items import atomic_write_json, atomic_write_text


def test_atomic_write_text_writes_content():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'plan.md'
        atomic_write_text(path, '# Plan\n')
        assert path.read_text(encoding='utf-8') == '# Plan\n'


def test_atomic_write_text_archives_before_overwrite():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        path = root / 'plan.md'
        archive_root = root / 'archive'
        path.write_text('old content\n', encoding='utf-8')

        atomic_write_text(path, 'new content\n', archive_root=archive_root)

        assert path.read_text(encoding='utf-8') == 'new content\n'
        zpath = archive_root / 'plan.zip'
        assert zpath.exists()
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            assert len(names) == 1
            assert zf.read(names[0]).decode('utf-8') == 'old content\n'


def test_atomic_write_text_skips_archive_on_first_creation():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        path = root / 'plan.md'
        archive_root = root / 'archive'

        atomic_write_text(path, 'first content\n', archive_root=archive_root)

        assert path.read_text(encoding='utf-8') == 'first content\n'
        assert not (archive_root / 'plan.zip').exists()


def test_atomic_write_json_sort_keys_false_by_default():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'item.json'
        atomic_write_json(path, {'b': 1, 'a': 2})
        # dict insertion order preserved when sort_keys is left at its default
        assert list(json.loads(path.read_text())) == list(json.loads(path.read_text()))
        assert path.read_text().index('"b"') < path.read_text().index('"a"')


def test_atomic_write_json_sort_keys_true_when_requested():
    """audit#1143 #1235 follow-up: itemdata_scrub.py relies on sort_keys=True
    for deterministic, diffable output — restored after the switch to
    atomic_write_json dropped the old write_text(..., sort_keys=True)."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'item.json'
        atomic_write_json(path, {'b': 1, 'a': 2}, sort_keys=True)
        text = path.read_text()
        assert text.index('"a"') < text.index('"b"')
