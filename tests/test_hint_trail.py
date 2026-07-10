"""Tests for identification_history trail — append_history_event + cmd_hint_trail."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tgw.items import append_history_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_cfg(root: Path) -> dict:
    return {
        'itemdata_root': root,
        'location_tree_root': root / 'by-location',
        'pretty': True,
    }


def make_item(root: Path, sku: str, **fields) -> Path:
    d = root / sku
    d.mkdir(parents=True, exist_ok=True)
    p = d / f'{sku}.json'
    p.write_text(json.dumps({'sku': sku, **fields}), encoding='utf-8')
    return p


def read_item(root: Path, sku: str) -> dict:
    return json.loads((root / sku / f'{sku}.json').read_text())


# ---------------------------------------------------------------------------
# append_history_event
# ---------------------------------------------------------------------------

def test_append_creates_history_list():
    item: dict = {}
    append_history_event(item, {'event': 'hint_set', 'hint': 'test'})
    assert 'identification_history' in item
    assert len(item['identification_history']) == 1
    assert item['identification_history'][0]['event'] == 'hint_set'


def test_append_adds_ts_if_absent():
    item: dict = {}
    append_history_event(item, {'event': 'hint_set', 'hint': 'x'})
    assert 'ts' in item['identification_history'][0]


def test_append_preserves_provided_ts():
    item: dict = {}
    append_history_event(item, {'event': 'hint_set', 'ts': '2000-01-01T00:00:00Z'})
    assert item['identification_history'][0]['ts'] == '2000-01-01T00:00:00Z'


def test_append_accumulates_multiple_events():
    item: dict = {}
    append_history_event(item, {'event': 'hint_set', 'hint': 'first'})
    append_history_event(item, {'event': 'ai_identify', 'round': 1})
    append_history_event(item, {'event': 'hint_set', 'hint': 'second'})
    assert len(item['identification_history']) == 3
    assert item['identification_history'][2]['hint'] == 'second'


def test_append_round_count():
    item: dict = {}
    append_history_event(item, {'event': 'ai_identify', 'round': 1, 'title': 'First Pass'})
    append_history_event(item, {'event': 'hint_set', 'hint': 'better hint'})
    append_history_event(item, {'event': 'ai_identify', 'round': 2, 'title': 'Second Pass'})
    ai_events = [e for e in item['identification_history'] if e['event'] == 'ai_identify']
    assert len(ai_events) == 2
    assert ai_events[0]['round'] == 1
    assert ai_events[1]['round'] == 2


# ---------------------------------------------------------------------------
# cmd_hint_trail (via api)
# ---------------------------------------------------------------------------

def test_hint_trail_empty(capsys):
    from tgw.api import cmd_hint_trail
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sku = 'tgw20260101000000001'
        make_item(root, sku)
        cfg = make_cfg(root)
        result = cmd_hint_trail(cfg, sku)
    assert result['ok'] is True
    assert result['count'] == 0
    assert result['history'] == []
    out = capsys.readouterr().out
    assert 'No identification history' in out


def test_hint_trail_missing_sku():
    from tgw.api import cmd_hint_trail
    with tempfile.TemporaryDirectory() as d:
        cfg = make_cfg(Path(d))
        result = cmd_hint_trail(cfg, 'tgw99999999999999999')
    assert result['ok'] is False
    assert 'not found' in result['error']


def test_hint_trail_with_events(capsys):
    from tgw.api import cmd_hint_trail
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sku = 'tgw20260101000000002'
        history = [
            {'ts': '2026-06-07T10:00:00Z', 'event': 'hint_set',
             'hint': 'Lego set', 'prev_hint': None, 'by': 'operator'},
            {'ts': '2026-06-07T10:01:00Z', 'event': 'ai_identify',
             'round': 1, 'model': 'qwen2.5vl:7b', 'prompt_type': 'hinted',
             'hint': 'Lego set', 'lookup_source': None,
             'title': 'Lego Classic Set', 'category': 'LEGO Sets',
             'condition': 'Good', 'ebay_category_id': 19016},
        ]
        make_item(root, sku, identification_history=history)
        cfg = make_cfg(root)
        result = cmd_hint_trail(cfg, sku)
    assert result['ok'] is True
    assert result['count'] == 2
    out = capsys.readouterr().out
    assert 'hint_set' in out
    assert 'Lego set' in out
    assert 'ai_identify' in out
    assert 'round 1' in out
    assert 'Lego Classic Set' in out


# ---------------------------------------------------------------------------
# cmd_hint records hint_set event (integration)
# ---------------------------------------------------------------------------

def test_cmd_hint_writes_trail_event():
    from unittest.mock import patch

    from tgw.api import cmd_hint
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sku = 'tgw20260101000000003'
        make_item(root, sku, ai_identified=False)
        cfg = make_cfg(root)

        # Patch out the state_machine enqueue so no DB needed
        with patch('tgw.queue.state_machine.init'), \
             patch('tgw.queue.state_machine.enqueue_job', return_value=42):
            result = cmd_hint(cfg, sku, 'thimbles', force=False)

        assert result['ok'] is True
        item = read_item(root, sku)
        assert 'identification_history' in item
        assert len(item['identification_history']) == 1
        ev = item['identification_history'][0]
        assert ev['event'] == 'hint_set'
        assert ev['hint'] == 'thimbles'
        assert ev['prev_hint'] is None
        assert ev['by'] == 'operator'
        assert 'ts' in ev


def test_cmd_hint_trail_records_prev_hint():
    from unittest.mock import patch

    from tgw.api import cmd_hint
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sku = 'tgw20260101000000004'
        make_item(root, sku, ai_hint='old hint', ai_identified=True)
        cfg = make_cfg(root)

        with patch('tgw.queue.state_machine.init'), \
             patch('tgw.queue.state_machine.enqueue_job', return_value=99):
            cmd_hint(cfg, sku, 'new hint', force=True)

        item = read_item(root, sku)
        ev = item['identification_history'][0]
        assert ev['hint'] == 'new hint'
        assert ev['prev_hint'] == 'old hint'
