"""
tgw.sku_migration — One-time SKU normalization (PP-ADD-005).

=============================================================================
CANONICAL FORMAT
=============================================================================
  tgw + YYYYMMDD + HHMMSS + s   (18 chars total)
  s = tenths-of-second (0–9), chosen for barcode label length.
  Example: tgw202001291640269

=============================================================================
MIGRATION RULES BY CLASS
=============================================================================
  A (len=20, non-1970)  truncate last 2 digits → sku[:18]
                        Collision resolution: if two Class A items share the
                        same tenths digit, the "loser" (later in sort order)
                        uses sku[:17]+sku[18] (hundredths digit instead).
                        7 such pairs exist in the dataset; all auto-resolved.

  B (len=20, tgw1970*)  Epoch-0 corruption — real date lost.
                        New format: tgw + 20150102 + 1970 + 3-char suffix
                        from original SKU (last 3 digits, with fallback to
                        alternate windows if collision). 2015 is best-guess
                        actual year; "1970" in the time field acknowledges
                        the corrupt source. Example:
                          tgw19700102105208728 → tgw201501021970728

  C (len=18, no _)      Already canonical — no change.

  D (len=18, has _)     tgw+YYYYMMDD+_+HHMMSS → strip _, append 0.
                        Example: tgw20200115_113609 → tgw202001151136090

  E (len=18, 2005-7yr)  YYMMDD-era items, actually from 2020.
                        Prepend 20 to expand 2-digit year; keep tenths only.
                        Example: tgw200503114925650 → tgw202005031149256

  F (len=17)            Missing tenths digit → append 0.
                        Example: tgw20180108202128 → tgw201801082021280

  G (len=19, anomaly)   Single item with non-digit in SKU; script reports
                        but does not migrate. Handle manually.

=============================================================================
LIVE EBAY ITEMS
=============================================================================
  Items with ebay_offer.offer_id or ebay_listing.listing_id are SKIPPED by
  default to prevent breaking active listings.

  For live items, the eBay rename path is:
    1. Delist  — POST /sell/inventory/v1/offer/{offer_id}/withdraw
    2. Local rename  (this script with --include-live-ebay)
    3. Re-create eBay inventory item at new SKU
    4. Re-create and publish offer
  Process in batches of ~50. Confirm each batch in Seller Hub before continuing.

=============================================================================
EXECUTION SEQUENCE
=============================================================================
  Step 1 — Pre-flight (always run first, no changes):
    tgw sku-migrate --check-collisions

  Step 2 — Fast classes (no live eBay, safe to bulk-run):
    tgw sku-migrate --class F,D,E,B --dry-run   # review
    tgw sku-migrate --class F,D,E,B --run        # ~229 items

  Step 3 — Class A without live eBay listings:
    tgw sku-migrate --class A --dry-run          # review (~26,423 items)
    tgw sku-migrate --class A --run

  Step 4 — Class A live eBay listings (slow batch, ~8,314 items):
    # delist batch in Seller Hub first, then:
    tgw sku-migrate --class A --include-live-ebay --run --limit 50
    # verify in Seller Hub, relist, then repeat

  Step 5 — Class G (1 item, disposed):
    # Rename manually or skip — item is in 'disposed' location.

  Step 6 — Rebuild catalogs after all steps:
    tgw build-all

=============================================================================
ROLLBACK
=============================================================================
  Every --run writes a timestamped manifest to /opt/TGW/var/log/:
    sku-migrate-<YYYYMMDDTHHMMSS>.json
  Format: {"renames": [{"old": "...", "new": "...", "class": "..."}, ...]}

  To roll back a batch: swap old/new in each entry and re-run rename_sku()
  in reverse order. The sku_history table also records every rename:
    psql -U tgw state_machine -c "SELECT * FROM sku_history ORDER BY changed_at DESC;"

=============================================================================
AFTER MIGRATION
=============================================================================
  Add SKU validation at intake (bundle_intake, multi_intake) to reject
  non-18-char SKUs at the point of entry. See PP-ADD-005 in the master plan.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2

from .config import location_dir, sku_dir, sku_json
from .items import atomic_write_json
from .queue import state_machine
from .resolver import iter_all_skus, load_item_doc

_MIGRATE_ARCHIVE = Path('/opt/TGW/var/migrate-archive')

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(sku: str) -> str:
    """Return class letter for a SKU."""
    L = len(sku)
    if L == 20:
        if sku.startswith('tgw1970'):
            return 'B'
        if sku[3:].isdigit():
            return 'A'
        return 'G'
    if L == 18:
        if '_' in sku:
            return 'D'
        if sku[3:7] in ('2005', '2006', '2007'):
            return 'E'
        if sku[3:].isdigit():
            return 'C'
        return 'G'
    if L == 17:
        return 'F'
    return 'G'


def canonical_sku(sku: str, use_hundredths: bool = False) -> Optional[str]:
    """
    Return the canonical 18-char SKU for a given input, or None for Class C/G.
    Does NOT check for collisions unless use_hundredths is set.

    use_hundredths: for Class A collision resolution — use the hundredths digit
    (sku[18]) instead of tenths (sku[17]) when the natural truncation collides.
    """
    cls = classify(sku)
    if cls == 'C':
        return None  # already canonical
    if cls == 'A':
        if use_hundredths:
            # sku[17] = tenths, sku[18] = hundredths — use hundredths instead
            return sku[:17] + sku[18]
        return sku[:18]
    if cls == 'B':
        # Primary: last 3 digits. Fallback suffix positions tried in build_migration_map.
        return f'tgw201501021970{sku[-3:]}'
    if cls == 'D':
        # tgw20200115_113609 → strip _, append 0
        body = sku[3:].replace('_', '')  # YYYYMMDDHHMMSS (14 chars)
        return f'tgw{body}0'
    if cls == 'E':
        # tgw200503114925650 → tgw + 20 + YYMMDD + HHMMSS + tenths
        body    = sku[3:]    # YYMMDDHHMMSS mmm (15 digits)
        date_yy = body[:6]   # YYMMDD
        time_s  = body[6:12] # HHMMSS
        tenths  = body[12]   # first ms digit = tenths
        return f'tgw20{date_yy}{time_s}{tenths}'
    if cls == 'F':
        return f'{sku}0'
    return None  # G — manual


# ---------------------------------------------------------------------------
# Collision check + auto-resolution
# ---------------------------------------------------------------------------

def build_migration_map(cfg: Dict[str, Any]) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """
    Build the full old→new SKU mapping for all non-canonical items, resolving
    Class A collisions automatically using the hundredths digit for the loser.

    Returns:
        migration_map: {old_sku: new_sku} for all items that need renaming
        unresolvable:  list of collision dicts that could not be auto-resolved
    """
    all_skus = sorted(iter_all_skus(cfg))
    current_set = set(all_skus)

    migration_map: Dict[str, str] = {}
    taken: Dict[str, str] = {}  # new_sku → old_sku that claimed it
    unresolvable: List[Dict[str, Any]] = []

    # Pass 1: non-A classes first (they have fixed canonical targets)
    for s in all_skus:
        cls = classify(s)
        if cls in ('C', 'G', 'A'):
            continue
        t = canonical_sku(s)
        if t is None:
            continue
        if t not in taken and t not in current_set:
            migration_map[s] = t
            taken[t] = s
        elif cls == 'B':
            # Class B fallback: try alternative 3-char windows from the original SKU
            resolved = False
            for start in range(len(s) - 3, 10, -1):
                alt = f'tgw201501021970{s[start:start+3]}'
                if alt not in taken and alt not in current_set:
                    migration_map[s] = alt
                    taken[alt] = s
                    resolved = True
                    break
            if not resolved:
                unresolvable.append({
                    'sku': s, 'target': t,
                    'conflict_type': 'b_collision_unresolvable',
                    'conflicts_with': taken.get(t, '?'),
                })
        else:
            unresolvable.append({
                'sku': s, 'target': t,
                'conflict_type': 'non_a_collision',
                'conflicts_with': taken.get(t, '?'),
            })

    # Mark currently canonical (Class C) as occupied
    for s in all_skus:
        if classify(s) == 'C':
            taken[s] = s

    # Pass 2: Class A — natural truncation first, then auto-resolve collisions
    # Sort so that within a collision pair the lower (earlier) SKU wins.
    for s in all_skus:
        if classify(s) != 'A':
            continue
        target = s[:18]
        if target not in taken and target not in current_set:
            migration_map[s] = target
            taken[target] = s
        else:
            # Try hundredths digit fallback
            alt = canonical_sku(s, use_hundredths=True)
            if alt and alt not in taken and alt not in current_set:
                migration_map[s] = alt
                taken[alt] = s
            else:
                unresolvable.append({
                    'sku': s,
                    'target': target,
                    'alt_target': alt,
                    'conflict_type': 'a_to_a_unresolvable',
                    'conflicts_with': taken.get(target, '?'),
                })

    return migration_map, unresolvable


def check_collisions(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the full collision check and return a structured report.
    Also shows the auto-resolved pairs so they can be reviewed.
    """
    all_skus = sorted(iter_all_skus(cfg))

    # Find raw A-to-A collisions (before resolution)
    raw_a_targets: Dict[str, str] = {}
    raw_collisions = []
    for s in all_skus:
        if classify(s) != 'A':
            continue
        t = s[:18]
        if t in raw_a_targets:
            raw_collisions.append({
                'winner': raw_a_targets[t],
                'loser':  s,
                'natural_target': t,
                'resolved_target': canonical_sku(s, use_hundredths=True),
            })
        else:
            raw_a_targets[t] = s

    # Build the resolved map and check for anything that can't be fixed
    _map, unresolvable = build_migration_map(cfg)

    return {
        'ok':                  len(unresolvable) == 0,
        'raw_a_collisions':    len(raw_collisions),
        'auto_resolved':       len(raw_collisions) - len(unresolvable),
        'unresolvable':        len(unresolvable),
        'safe_to_migrate':     len(unresolvable) == 0,
        'resolved_pairs':      raw_collisions,
        'unresolvable_detail': unresolvable,
    }


# ---------------------------------------------------------------------------
# sku_history table
# ---------------------------------------------------------------------------

def ensure_sku_history_table(cfg: Dict[str, Any]) -> None:
    """Create sku_history table if it doesn't exist."""
    sql_path = Path(__file__).parent / 'queue' / 'sku_history.sql'
    sql = sql_path.read_text(encoding='utf-8')
    with psycopg2.connect(cfg['postgres_dsn']) as con:
        with con.cursor() as cur:
            cur.execute(sql)
        con.commit()


def _record_history(cur, sku_old: str, sku_new: str, cls: str,
                    had_ebay: bool, notes: str = '') -> None:
    reason = f'normalize_class_{cls.lower()}'
    cur.execute(
        """
        INSERT INTO sku_history
            (sku_old, sku_new, changed_at, change_reason, changed_by,
             had_ebay_listing, notes)
        VALUES (%s, %s, NOW(), %s, %s, %s, %s)
        """,
        (sku_old, sku_new, reason, 'sku_migrate_script', had_ebay, notes),
    )


# ---------------------------------------------------------------------------
# Single-item rename
# ---------------------------------------------------------------------------

def _has_live_ebay(item: Dict[str, Any]) -> bool:
    return bool(
        item.get('ebay_offer', {}).get('offer_id') or
        item.get('ebay_listing', {}).get('listing_id')
    )


def rename_sku(cfg: Dict[str, Any], old_sku: str, new_sku: str,
               cls: str, dry_run: bool = True) -> Dict[str, Any]:
    """
    Rename one item: move directory, rewrite JSON, update location symlink,
    record sku_history, enqueue catalog_rebuild.

    Returns result dict with ok/status.
    """
    old_dir  = sku_dir(cfg, old_sku)
    new_dir  = sku_dir(cfg, new_sku)
    old_json = old_dir / f'{old_sku}.json'

    if not old_dir.exists():
        return {'ok': False, 'sku': old_sku, 'error': 'source dir not found'}
    if new_dir.exists():
        return {'ok': False, 'sku': old_sku, 'error': f'target dir already exists: {new_sku}'}

    item = load_item_doc(old_json)
    had_ebay = _has_live_ebay(item)
    location = str(item.get('location', '')).strip()

    if dry_run:
        return {
            'ok': True, 'dry_run': True,
            'old': old_sku, 'new': new_sku, 'class': cls,
            'had_ebay': had_ebay, 'location': location,
        }

    # Archive snapshot before any mutation — recovery baseline
    try:
        _MIGRATE_ARCHIVE.mkdir(parents=True, exist_ok=True)
        archive_path = _MIGRATE_ARCHIVE / f'{old_sku}.json'
        atomic_write_json(archive_path, item)
    except Exception as arc_exc:
        log.warning('rename_sku: could not write migrate-archive for %s: %s', old_sku, arc_exc)

    try:
        # 1. Move directory
        shutil.move(str(old_dir), str(new_dir))

        # 2. Rename JSON file and update sku field inside it
        old_json_in_new = new_dir / f'{old_sku}.json'
        new_json_final  = new_dir / f'{new_sku}.json'
        old_json_in_new.rename(new_json_final)

        item['sku'] = new_sku
        atomic_write_json(new_json_final, item, pretty=cfg.get('pretty', True))

        # 3. Update location symlink
        if location:
            try:
                link_dir = location_dir(cfg, location)
            except ValueError as exc:
                log.warning("rename_sku: unsafe location %r for %s: %s", location, new_sku, exc)
            else:
                old_link  = link_dir / old_sku
                new_link  = link_dir / new_sku
                if old_link.exists() or old_link.is_symlink():
                    old_link.unlink()
                if link_dir.exists():
                    if new_link.exists() or new_link.is_symlink():
                        new_link.unlink()
                    os.symlink(new_dir, new_link)

        # 4. Record in sku_history
        with psycopg2.connect(cfg['postgres_dsn']) as con:
            with con.cursor() as cur:
                _record_history(cur, old_sku, new_sku, cls, had_ebay)
            con.commit()

        # 5. Enqueue coalesced catalog_rebuild
        try:
            state_machine.enqueue_job(
                queue_name='catalog_rebuild',
                payload={'reason': f'sku_migrate:{new_sku}'},
                dedupe_key='catalog_rebuild:pending',
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except Exception:
            pass  # already queued

    except Exception as e:
        return {
            'ok': False, 'sku': old_sku,
            'error': f'{type(e).__name__}: {e}',
        }

    return {
        'ok': True, 'dry_run': False,
        'old': old_sku, 'new': new_sku, 'class': cls,
        'had_ebay': had_ebay, 'location': location,
    }


# ---------------------------------------------------------------------------
# Bulk migration runner
# ---------------------------------------------------------------------------

def run_migration(
    cfg: Dict[str, Any],
    classes: List[str],
    dry_run: bool = True,
    include_live_ebay: bool = False,
    limit: int = 0,
    manifest_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Migrate SKUs for the given class list.

    Args:
        classes:           list of class letters to process, e.g. ['F','D','E','B']
        dry_run:           if True, report only — no filesystem changes
        include_live_ebay: if False (default), skip items with live eBay listings
        limit:             max items to process (0 = unlimited)
        manifest_path:     where to write the rollback manifest JSON

    Returns summary dict.
    """
    started = time.time()
    classes_set = set(c.upper() for c in classes)

    if not dry_run:
        ensure_sku_history_table(cfg)
        state_machine.init(cfg['postgres_dsn'])

    # Build the full collision-resolved migration map upfront
    migration_map, unresolvable = build_migration_map(cfg)

    if unresolvable:
        return {
            'ok': False,
            'error': f'{len(unresolvable)} unresolvable collision(s) — run --check-collisions',
            'unresolvable': unresolvable,
        }

    planned: List[Tuple[str, str, str]] = []  # (old, new, cls)
    skipped_canonical = 0
    skipped_live_ebay = 0
    skipped_manual    = 0

    for sku, target in migration_map.items():
        cls = classify(sku)
        if cls not in classes_set:
            if cls == 'C':
                skipped_canonical += 1
            continue
        if cls == 'G':
            skipped_manual += 1
            continue

        # Check live eBay unless explicitly included
        if not include_live_ebay:
            json_path = sku_json(cfg, sku)
            if json_path.exists():
                try:
                    item = load_item_doc(json_path)
                    if _has_live_ebay(item):
                        skipped_live_ebay += 1
                        continue
                except Exception:
                    pass

        planned.append((sku, target, cls))
        if limit and len(planned) >= limit:
            break

    # Write rollback manifest before making any changes
    manifest: Dict[str, Any] = {
        'generated_at': datetime.now(tz=timezone.utc).isoformat(),
        'dry_run':       dry_run,
        'classes':       sorted(classes_set),
        'include_live_ebay': include_live_ebay,
        'renames': [{'old': o, 'new': n, 'class': c} for o, n, c in planned],
    }
    if manifest_path and not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        log.info('rollback manifest written to %s', manifest_path)

    # Execute
    results = []
    errors  = []
    for old_sku, new_sku, cls in planned:
        try:
            r = rename_sku(cfg, old_sku, new_sku, cls, dry_run=dry_run)
        except Exception as e:
            r = {'ok': False, 'sku': old_sku, 'error': f'unhandled: {type(e).__name__}: {e}'}
        if r['ok']:
            results.append(r)
        else:
            errors.append(r)
            log.error('rename failed %s → %s: %s', old_sku, new_sku, r.get('error'))

    elapsed = round(time.time() - started, 2)
    return {
        'ok':                len(errors) == 0,
        'dry_run':           dry_run,
        'classes':           sorted(classes_set),
        'include_live_ebay': include_live_ebay,
        'planned':           len(planned),
        'succeeded':         len(results),
        'errors':            len(errors),
        'skipped_canonical': skipped_canonical,
        'skipped_live_ebay': skipped_live_ebay,
        'skipped_manual':    skipped_manual,
        'elapsed_seconds':   elapsed,
        'error_details':     errors[:20] if errors else [],
        'sample':            [{'old': r['old'], 'new': r['new'], 'class': r['class']}
                              for r in results[:10]],
        'manifest_path':     str(manifest_path) if manifest_path and not dry_run else None,
    }


# ---------------------------------------------------------------------------
# Collision report
# ---------------------------------------------------------------------------

def collision_report(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Run collision check and return structured report."""
    collisions = check_collisions(cfg)
    return {
        'ok': collisions['ok'],
        'total': collisions['raw_a_collisions'],
        'by_type': {
            'auto_resolved': collisions['auto_resolved'],
            'unresolvable': collisions['unresolvable'],
        },
        'collisions': collisions['resolved_pairs'][:50],
        'safe_to_migrate': collisions['safe_to_migrate'],
    }
