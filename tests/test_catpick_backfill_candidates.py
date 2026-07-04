"""PP-CATPICK-001 Phase 1 (todo #1079) — category_candidates backfill logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from catpick_backfill_candidates import _ancestor_path  # noqa: E402


def _index(*nodes):
    """nodes: (id, name, parent_id) tuples."""
    return {nid: {'id': nid, 'name': name, 'parent_id': parent} for nid, name, parent in nodes}


def test_ancestor_path_root_first_leaf_last():
    idx = _index(
        ('1', 'Collectibles', None),
        ('2', 'Kitchen & Home', '1'),
        ('3', 'Mugs', '2'),
    )
    assert _ancestor_path(idx, '3') == ['Collectibles', 'Kitchen & Home', 'Mugs']


def test_ancestor_path_top_level_category():
    idx = _index(('1', 'Books', None))
    assert _ancestor_path(idx, '1') == ['Books']


def test_ancestor_path_unknown_id_falls_back_to_bare_id():
    idx = _index(('1', 'Books', None))
    assert _ancestor_path(idx, '99999') == ['99999']


def test_ancestor_path_breaks_cycle_defensively():
    """A malformed cache with a parent cycle must not infinite-loop."""
    idx = _index(('1', 'A', '2'), ('2', 'B', '1'))
    result = _ancestor_path(idx, '1')
    assert len(result) <= 2  # terminates, doesn't hang or grow unbounded
