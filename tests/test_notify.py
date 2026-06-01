"""Tests for tgw.notify."""

ffrom __future__ import annotations

import json
import tempfile
from pathlib import Path

from tgw.notify import Notifier, notify   # remove: configure  (F401)

def test_notifier_log_backend_does_not_raise():
    n = Notifier({'backends': ['log'], 'enabled': True})
    n.send('Test', 'message', level='info')


def test_notifier_file_backend_writes():
    with tempfile.TemporaryDirectory() as d:
        fpath = Path(d) / 'notify.jsonl'
        n = Notifier({'backends': ['file'], 'enabled': True, 'file': str(fpath)})
        n.send('Hello', 'World', level='info')
        assert fpath.exists()
        record = json.loads(fpath.read_text())
        assert record['title'] == 'Hello'
        assert record['message'] == 'World'
        assert record['level'] == 'info'


def test_notifier_min_level_filters():
    with tempfile.TemporaryDirectory() as d:
        fpath = Path(d) / 'notify.jsonl'
        n = Notifier({'backends': ['file'], 'enabled': True,
                      'file': str(fpath), 'min_level': 'error'})
        n.send('Low priority', 'ignored', level='info')
        assert not fpath.exists()
        n.send('High priority', 'written', level='error')
        assert fpath.exists()


def test_notifier_disabled():
    with tempfile.TemporaryDirectory() as d:
        fpath = Path(d) / 'notify.jsonl'
        n = Notifier({'backends': ['file'], 'enabled': False, 'file': str(fpath)})
        n.send('Should not appear', level='error')
        assert not fpath.exists()


def test_notifier_callable():
    n = Notifier({'backends': ['log'], 'enabled': True})
    n('Title', 'message')   # __call__ interface


def test_module_level_notify_does_not_raise():
    notify('Platform test', 'from test suite', level='info')
