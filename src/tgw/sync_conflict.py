"""
tgw.sync_conflict — Syncthing conflict-file scanner (PP-PORTABLE-CATALOG-001 P3).

Scans configured sync roots for ``*.sync-conflict-*`` files. Each conflict is
compared byte-for-byte to its canonical counterpart:

  - Identical    → auto-discard (delete the conflict copy, no data lost)
  - Divergent    → move to ``inbox/review/`` and create an operator todo
  - No canonical → treat as divergent (unknown provenance; never auto-delete)

Zero-data-loss invariant: unique content is NEVER deleted under any code path.

Scan roots default to ``[plan_vault_path, itemdata_root]`` and are extended
via ``sync_conflict_roots`` in ``tgw-api-config.json``.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

# Syncthing conflict filename: <stem>.sync-conflict-YYYYMMDD-HHMMSS-HASH[.<ext>]
_CONFLICT_RE = re.compile(
    r'^(.+?)\.sync-conflict-\d{8}-\d{6}-[A-Za-z0-9]+(\.[^.]+)?$',
)


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def canonical_name(filename: str) -> Optional[str]:
    """Return the canonical filename for a sync-conflict filename.

    Returns None if ``filename`` does not match the Syncthing conflict pattern.

    Examples::

        canonical_name('community-plugins.sync-conflict-20260601-120000-ABCDEF.json')
        # → 'community-plugins.json'

        canonical_name('directorysizes.sync-conflict-20260517-134153-Y3YVMPP')
        # → 'directorysizes'

        canonical_name('normal.json')  # → None
    """
    m = _CONFLICT_RE.match(filename)
    if not m:
        return None
    stem = m.group(1)
    ext  = m.group(2) or ''
    return stem + ext


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_conflict(conflict_path: Path) -> str:
    """Classify one conflict file against its canonical counterpart.

    Returns one of:
      ``'identical'``    — byte-for-byte match; safe to discard
      ``'divergent'``    — differs from canonical; needs operator review
      ``'no_canonical'`` — canonical file does not exist; needs operator review
    """
    canon = canonical_name(conflict_path.name)
    if canon is None:
        return 'no_canonical'

    canonical_path = conflict_path.parent / canon
    if not canonical_path.exists():
        return 'no_canonical'

    if conflict_path.read_bytes() == canonical_path.read_bytes():
        return 'identical'

    return 'divergent'


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_conflict(
    conflict_path: Path,
    review_dir: Path,
    *,
    dry_run: bool = False,
    add_todo_fn: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Resolve one conflict file.

    Returns a dict with keys:
      ``action``    — 'discarded', 'flagged', or 'skipped' (dry_run)
      ``conflict``  — Path of the conflict file
      ``canonical`` — Path of the canonical file (or None)
      ``reason``    — 'identical', 'divergent', 'no_canonical'
      ``dest``      — Path the file was moved to (flagged action only)
    """
    verdict = classify_conflict(conflict_path)
    canon_name = canonical_name(conflict_path.name)
    _canon_path = (conflict_path.parent / canon_name) if canon_name else None
    canonical = _canon_path if (_canon_path and _canon_path.exists()) else None

    result: Dict[str, Any] = {
        'conflict':  conflict_path,
        'canonical': canonical,
        'reason':    verdict,
        'dest':      None,
    }

    if verdict == 'identical':
        if not dry_run:
            conflict_path.unlink()
            log.info('sync_conflict: discarded identical %s', conflict_path.name)
        result['action'] = 'skipped' if dry_run else 'discarded'
        return result

    # Divergent or no_canonical → move to review and create todo
    if dry_run:
        result['action'] = 'skipped'
        return result

    review_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(review_dir, conflict_path.name)
    conflict_path.rename(dest)
    log.warning('sync_conflict: flagged %s → %s', conflict_path.name, dest)

    if add_todo_fn:
        try:
            add_todo_fn(
                f'Review sync-conflict: {conflict_path.name}\n'
                f'Moved to: {dest}\n'
                f'Canonical: {canonical}\n'
                f'Reason: {verdict}'
            )
        except Exception as exc:
            log.warning('sync_conflict: todo_add failed for %s: %s', conflict_path.name, exc)

    result['action'] = 'flagged'
    result['dest']   = dest
    return result


def _unique_dest(review_dir: Path, filename: str) -> Path:
    """Return a non-colliding path inside review_dir for filename."""
    dest = review_dir / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    i = 1
    while True:
        candidate = review_dir / f'{stem}-{i}{suffix}'
        if not candidate.exists():
            return candidate
        i += 1


# ---------------------------------------------------------------------------
# Scan helpers
# ---------------------------------------------------------------------------

def _iter_conflicts(root: Path):
    """Yield all sync-conflict files under root (recursive)."""
    if not root.exists():
        return
    for path in root.rglob('*'):
        if path.is_file() and canonical_name(path.name) is not None:
            yield path


def count_conflicts(roots: List[Path]) -> int:
    """Return the total count of unresolved sync-conflict files across roots."""
    return sum(1 for root in roots for _ in _iter_conflicts(root))


# ---------------------------------------------------------------------------
# Full-scan entry point
# ---------------------------------------------------------------------------

def _default_add_todo(body: str) -> None:
    from tgw.todo import todo_add
    todo_add(agent='admin', body=body, priority=30, source='sync_conflict',
             pp_ref='PP-PORTABLE-CATALOG-001')


def run_scan(
    cfg: Dict[str, Any],
    *,
    dry_run: bool = False,
    add_todo_fn: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Scan all configured sync roots and resolve every conflict found.

    Returns a result dict with counts and per-file details.
    ``dry_run=True`` classifies without mutating anything.

    When ``add_todo_fn`` is None and dry_run is False, uses the live todo DB.
    """
    t0 = time.time()

    roots: List[Path] = cfg.get('sync_conflict_roots') or []
    review_dir: Path  = cfg['plan_inbox_path'] / 'review'

    todo_fn = add_todo_fn
    if todo_fn is None and not dry_run:
        todo_fn = _default_add_todo

    total = discarded = flagged = 0
    details = []

    for root in roots:
        for conflict in list(_iter_conflicts(root)):
            total += 1
            res = resolve_conflict(
                conflict, review_dir,
                dry_run=dry_run,
                add_todo_fn=todo_fn,
            )
            if res['action'] == 'discarded':
                discarded += 1
            elif res['action'] == 'flagged':
                flagged += 1
            details.append({
                'file':   str(res['conflict']),
                'action': res['action'],
                'reason': res['reason'],
            })

    elapsed = round(time.time() - t0, 3)
    log.info(
        'sync_conflict scan: %d found, %d discarded, %d flagged, %.1fs',
        total, discarded, flagged, elapsed,
    )

    return {
        'ok':           True,
        'artifact':     'sync_conflict_scan',
        'dry_run':      dry_run,
        'roots_scanned': [str(r) for r in roots],
        'total':        total,
        'discarded':    discarded,
        'flagged':      flagged,
        'skipped':      total - discarded - flagged,
        'details':      details,
        'elapsed_seconds': elapsed,
    }
