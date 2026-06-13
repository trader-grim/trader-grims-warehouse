"""Tests for tgw.sync_conflict — PP-PORTABLE-CATALOG-001 P3.

All tests are offline and filesystem-only (no DB, no tgw-api calls).
The todo callback is passed explicitly so no real DB is touched.
"""

from __future__ import annotations

from tgw.sync_conflict import (
    canonical_name,
    classify_conflict,
    count_conflicts,
    resolve_conflict,
    run_scan,
)

# ---------------------------------------------------------------------------
# canonical_name
# ---------------------------------------------------------------------------

class TestCanonicalName:

    def test_json_extension(self):
        assert canonical_name(
            'community-plugins.sync-conflict-20260601-120000-ABCDEF.json'
        ) == 'community-plugins.json'

    def test_sku_json(self):
        assert canonical_name(
            'tgw20170509093557075.sync-conflict-20250803-145705-KWJ6FX3.json'
        ) == 'tgw20170509093557075.json'

    def test_no_extension(self):
        assert canonical_name(
            'directorysizes.sync-conflict-20260517-134153-Y3YVMPP'
        ) == 'directorysizes'

    def test_lowercase_hash(self):
        # Syncthing hashes are uppercase, but be lenient
        assert canonical_name(
            'foo.sync-conflict-20260101-120000-abc123.txt'
        ) == 'foo.txt'

    def test_not_conflict_file(self):
        assert canonical_name('community-plugins.json') is None
        assert canonical_name('normal-file.txt') is None
        assert canonical_name('sync-conflict-itself') is None
        assert canonical_name('') is None

    def test_hyphenated_stem(self):
        assert canonical_name(
            'my-file-name.sync-conflict-20260101-000000-AABBCC.md'
        ) == 'my-file-name.md'


# ---------------------------------------------------------------------------
# classify_conflict
# ---------------------------------------------------------------------------

class TestClassifyConflict:

    def test_identical_content(self, tmp_path):
        data = b'{"key": "value"}'
        canonical = tmp_path / 'foo.json'
        canonical.write_bytes(data)
        conflict = tmp_path / 'foo.sync-conflict-20260101-120000-ABCDEF.json'
        conflict.write_bytes(data)
        assert classify_conflict(conflict) == 'identical'

    def test_divergent_content(self, tmp_path):
        (tmp_path / 'foo.json').write_bytes(b'{"a": 1}')
        conflict = tmp_path / 'foo.sync-conflict-20260101-120000-ABCDEF.json'
        conflict.write_bytes(b'{"a": 2}')
        assert classify_conflict(conflict) == 'divergent'

    def test_no_canonical(self, tmp_path):
        conflict = tmp_path / 'foo.sync-conflict-20260101-120000-ABCDEF.json'
        conflict.write_bytes(b'data')
        assert classify_conflict(conflict) == 'no_canonical'


# ---------------------------------------------------------------------------
# resolve_conflict
# ---------------------------------------------------------------------------

class TestResolveConflict:

    def _review(self, tmp_path):
        r = tmp_path / 'inbox' / 'review'
        r.mkdir(parents=True)
        return r

    def test_identical_discards_file(self, tmp_path):
        data = b'same content'
        (tmp_path / 'foo.json').write_bytes(data)
        conflict = tmp_path / 'foo.sync-conflict-20260601-120000-AABBCC.json'
        conflict.write_bytes(data)

        result = resolve_conflict(conflict, self._review(tmp_path))
        assert result['action'] == 'discarded'
        assert result['reason'] == 'identical'
        assert not conflict.exists()

    def test_divergent_moves_to_review(self, tmp_path):
        (tmp_path / 'foo.json').write_bytes(b'canonical')
        conflict = tmp_path / 'foo.sync-conflict-20260601-120000-AABBCC.json'
        conflict.write_bytes(b'conflict version')
        todos = []

        result = resolve_conflict(conflict, self._review(tmp_path),
                                  add_todo_fn=todos.append)
        assert result['action'] == 'flagged'
        assert result['reason'] == 'divergent'
        assert not conflict.exists()
        assert result['dest'].exists()
        assert result['dest'].parent.name == 'review'
        assert len(todos) == 1
        assert 'foo.sync-conflict' in todos[0]

    def test_no_canonical_moves_to_review(self, tmp_path):
        conflict = tmp_path / 'foo.sync-conflict-20260601-120000-AABBCC.json'
        conflict.write_bytes(b'orphan data')
        todos = []

        result = resolve_conflict(conflict, self._review(tmp_path),
                                  add_todo_fn=todos.append)
        assert result['action'] == 'flagged'
        assert result['reason'] == 'no_canonical'
        assert not conflict.exists()
        assert result['canonical'] is None
        assert len(todos) == 1

    def test_dry_run_makes_no_changes(self, tmp_path):
        data = b'same'
        (tmp_path / 'foo.json').write_bytes(data)
        conflict = tmp_path / 'foo.sync-conflict-20260601-120000-AABBCC.json'
        conflict.write_bytes(data)

        result = resolve_conflict(conflict, self._review(tmp_path), dry_run=True)
        assert result['action'] == 'skipped'
        assert conflict.exists()  # not deleted

    def test_dry_run_divergent_no_changes(self, tmp_path):
        (tmp_path / 'foo.json').write_bytes(b'canonical')
        conflict = tmp_path / 'foo.sync-conflict-20260601-120000-AABBCC.json'
        conflict.write_bytes(b'different')

        todos = []
        result = resolve_conflict(conflict, self._review(tmp_path),
                                  dry_run=True, add_todo_fn=todos.append)
        assert result['action'] == 'skipped'
        assert conflict.exists()  # not moved
        assert len(todos) == 0   # no todo created in dry-run

    def test_collision_avoidance(self, tmp_path):
        review = self._review(tmp_path)
        (tmp_path / 'foo.json').write_bytes(b'canonical')

        # Manually place a file at the naive dest to force collision
        (review / 'foo.sync-conflict-20260601-120000-AABBCC.json').write_bytes(b'prior')

        conflict = tmp_path / 'foo.sync-conflict-20260601-120000-AABBCC.json'
        conflict.write_bytes(b'new version')

        result = resolve_conflict(conflict, review, add_todo_fn=lambda _: None)
        assert result['action'] == 'flagged'
        # dest must differ from the pre-existing file
        assert result['dest'].name != 'foo.sync-conflict-20260601-120000-AABBCC.json'
        assert result['dest'].exists()


# ---------------------------------------------------------------------------
# count_conflicts
# ---------------------------------------------------------------------------

def test_count_conflicts(tmp_path):
    root = tmp_path / 'vault'
    root.mkdir()
    (root / 'a.sync-conflict-20260101-120000-ABC.json').write_bytes(b'x')
    (root / 'b.sync-conflict-20260101-120000-DEF.json').write_bytes(b'y')
    (root / 'normal.json').write_bytes(b'not a conflict')

    sub = root / '.obsidian'
    sub.mkdir()
    (sub / 'c.sync-conflict-20260101-120000-GHI.json').write_bytes(b'z')

    assert count_conflicts([root]) == 3
    assert count_conflicts([]) == 0


def test_count_conflicts_nonexistent_root(tmp_path):
    assert count_conflicts([tmp_path / 'does-not-exist']) == 0


# ---------------------------------------------------------------------------
# run_scan
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    inbox = vault / 'inbox'
    (inbox / 'review').mkdir(parents=True)
    return {
        'plan_inbox_path': inbox,
        'sync_conflict_roots': [vault],
    }


def test_run_scan_empty_roots(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg['sync_conflict_roots'] = []
    result = run_scan(cfg, dry_run=True)
    assert result['ok'] is True
    assert result['total'] == 0


def test_run_scan_discards_identical(tmp_path):
    cfg = _make_cfg(tmp_path)
    root = cfg['sync_conflict_roots'][0]
    data = b'same bytes'
    (root / 'foo.json').write_bytes(data)
    (root / 'foo.sync-conflict-20260101-120000-AABBCC.json').write_bytes(data)

    result = run_scan(cfg, add_todo_fn=lambda _: None)
    assert result['total'] == 1
    assert result['discarded'] == 1
    assert result['flagged'] == 0


def test_run_scan_flags_divergent(tmp_path):
    cfg = _make_cfg(tmp_path)
    root = cfg['sync_conflict_roots'][0]
    (root / 'bar.json').write_bytes(b'canonical')
    (root / 'bar.sync-conflict-20260101-120000-AABBCC.json').write_bytes(b'divergent')

    flagged_todos = []
    result = run_scan(cfg, add_todo_fn=flagged_todos.append)
    assert result['flagged'] == 1
    assert result['discarded'] == 0
    assert len(flagged_todos) == 1


def test_run_scan_dry_run_no_mutations(tmp_path):
    cfg = _make_cfg(tmp_path)
    root = cfg['sync_conflict_roots'][0]
    data = b'same'
    (root / 'x.json').write_bytes(data)
    conflict = root / 'x.sync-conflict-20260101-120000-AABBCC.json'
    conflict.write_bytes(data)

    result = run_scan(cfg, dry_run=True)
    assert result['dry_run'] is True
    assert result['total'] == 1
    assert conflict.exists()  # not deleted in dry-run


def test_run_scan_multiple_roots(tmp_path):
    root_a = tmp_path / 'vault'
    root_b = tmp_path / 'catalog'
    root_a.mkdir()
    root_b.mkdir()
    inbox = tmp_path / 'inbox'
    (inbox / 'review').mkdir(parents=True)

    data = b'same'
    for root in (root_a, root_b):
        (root / 'f.json').write_bytes(data)
        (root / 'f.sync-conflict-20260101-120000-AABBCC.json').write_bytes(data)

    cfg = {
        'plan_inbox_path': inbox,
        'sync_conflict_roots': [root_a, root_b],
    }
    result = run_scan(cfg, add_todo_fn=lambda _: None)
    assert result['total'] == 2
    assert result['discarded'] == 2


# ---------------------------------------------------------------------------
# Acceptance case: .obsidian/community-plugins pattern
# ---------------------------------------------------------------------------

def test_acceptance_obsidian_community_plugins(tmp_path):
    """Classify a vault .obsidian/community-plugins.sync-conflict-*.json artifact.

    Simulates the live acceptance case described in the task brief. Tests both
    'identical to canonical' and 'divergent from canonical' paths.
    """
    obsidian = tmp_path / 'vault' / '.obsidian'
    obsidian.mkdir(parents=True)
    inbox = tmp_path / 'vault' / 'inbox'
    (inbox / 'review').mkdir(parents=True)

    conflict_name = 'community-plugins.sync-conflict-20260601-120000-ABCDEF.json'
    canonical_content = b'["dataview","templater-obsidian"]'

    # Case 1: conflict is identical to canonical → discard
    canonical = obsidian / 'community-plugins.json'
    canonical.write_bytes(canonical_content)
    conflict = obsidian / conflict_name
    conflict.write_bytes(canonical_content)

    result = resolve_conflict(conflict, inbox / 'review')
    assert result['action'] == 'discarded'
    assert result['reason'] == 'identical'
    assert not conflict.exists()
    assert canonical.exists()  # canonical untouched

    # Case 2: conflict differs → flagged
    conflict.write_bytes(b'["dataview"]')  # recreate with different content

    todos = []
    result = resolve_conflict(conflict, inbox / 'review',
                              add_todo_fn=todos.append)
    assert result['action'] == 'flagged'
    assert result['reason'] == 'divergent'
    assert (inbox / 'review' / conflict_name).exists()
    assert canonical.exists()  # canonical still untouched
    assert len(todos) == 1


# ---------------------------------------------------------------------------
# Health check integration (no DB needed)
# ---------------------------------------------------------------------------

def test_health_check_no_conflicts(tmp_path):
    from tgw.health import check_sync_conflicts
    vault = tmp_path / 'vault'
    vault.mkdir()
    cfg = {'sync_conflict_roots': [vault]}
    result = check_sync_conflicts(cfg)
    assert result['ok'] is True
    assert result['conflict_count'] == 0


def test_health_check_with_conflicts(tmp_path):
    from tgw.health import check_sync_conflicts
    vault = tmp_path / 'vault'
    vault.mkdir()
    (vault / 'foo.sync-conflict-20260101-120000-AABBCC.json').write_bytes(b'x')
    cfg = {'sync_conflict_roots': [vault]}
    result = check_sync_conflicts(cfg)
    assert result['ok'] is True
    assert result['warn'] is True
    assert result['conflict_count'] == 1
    assert 'unresolved' in result['detail']
