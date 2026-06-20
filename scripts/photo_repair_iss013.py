#!/usr/bin/env python3
"""
photo_repair_iss013.py — Repair ISS-013: rename misnamed <SKU>-alt.jpg files.

BACKGROUND
----------
The alt-text worker renamed <SKU>.jpg → <SKU>-alt.jpg instead of copying it,
leaving the -alt file with the wrong name. The original primary photo (in the
older tgwYYYYMMDD_HHMMSS.jpg naming format) was already present in the directory
and is not affected; it is the true primary and stays untouched.

The correct final state for each affected item:
  BEFORE: <SKU>-alt.jpg  (misnamed)      + tgwYYYYMMDD_HHMMSS.jpg (primary, unchanged)
  AFTER:  tgwYYYYMMDD_HHMMSS-alt.jpg    + tgwYYYYMMDD_HHMMSS.jpg

The original photo and the misnamed -alt.jpg have identical content (same file,
just renamed). The matching original is identified by file size, then confirmed
by comparing the first 64 KB of both files.

619 items are affected. All are 2026 SKUs. All have exactly one size-matching
original photo in their directory.

SAFETY DESIGN
-------------
- Dry-run by default. Pass --execute to rename any files.
- Requires a Btrfs snapshot taken within SNAPSHOT_MAX_AGE_MINUTES before --execute.
- Renames only. Never creates new files.
- Will not clobber an existing <original>-alt.jpg.
- Confirms both files are valid JPEGs before renaming.
- Confirms files share identical first 64 KB (content match, not just size).
- After rename: verifies old path gone, new path exists, size unchanged.
- Removes a wrongly-created <SKU>.jpg if present (leftover from prior repair attempt).
- Writes a timestamped log to /opt/TGW/var/log/.

USAGE
-----
  sudo -u tgw python3 scripts/photo_repair_iss013.py --dry-run
  sudo -u tgw python3 scripts/photo_repair_iss013.py --dry-run --sku tgw202604042035007
  sudo -u tgw python3 scripts/photo_repair_iss013.py --execute
  sudo -u tgw python3 scripts/photo_repair_iss013.py --execute --sku tgw202604042035007
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ITEMDATA_ROOT = Path('/opt/TGW/data/ItemData')
# Local snapshots are pruned immediately after send (LOCAL_KEEP=2).
# Check the backup target, which retains all received snapshots.
SNAPSHOT_DIRS = [
    Path('/opt/TGW/.snapshots'),           # local (may be pruned)
    Path('/home/snapshot/TGW-SNAPSHOT-0'), # backup target (authoritative)
]
LOG_DIR = Path('/opt/TGW/var/log')

# A snapshot within this many minutes is required before --execute.
# Hourly timer guarantees one every 60 min; 90 min gives one full cycle of headroom.
SNAPSHOT_MAX_AGE_MINUTES = 90

# Bytes to compare for content confirmation (well within any photo; fast enough for ~600 files).
CONTENT_CHECK_BYTES = 65536

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log = logging.getLogger('photo_repair_iss013')


def is_valid_jpeg(path: Path) -> bool:
    try:
        with open(path, 'rb') as f:
            return f.read(3) == b'\xff\xd8\xff'
    except OSError:
        return False


def first_n_bytes(path: Path, n: int) -> bytes | None:
    try:
        with open(path, 'rb') as f:
            return f.read(n)
    except OSError:
        return None


def check_recent_snapshot() -> tuple[bool, str]:
    """
    Verify a Btrfs snapshot exists and is within SNAPSHOT_MAX_AGE_MINUTES.
    Checks all SNAPSHOT_DIRS; newest stamp across all directories wins.
    """
    pattern = re.compile(r'^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})$')
    candidates = []

    for snap_dir in SNAPSHOT_DIRS:
        try:
            for entry in snap_dir.iterdir():
                m = pattern.match(entry.name)
                if m and entry.is_dir():
                    try:
                        snap_dt = datetime(
                            int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(m.group(4)), int(m.group(5)),
                        )
                        candidates.append((snap_dt, entry.name, snap_dir))
                    except ValueError:
                        pass
        except FileNotFoundError:
            pass

    if not candidates:
        return False, (
            'no snapshots found in any snapshot directory.\n\n'
            '  Take a snapshot first:\n'
            '    sudo systemctl start tgw-snapshot.service\n\n'
            '  Then re-run this script.'
        )

    latest_dt, latest_name, latest_dir = max(candidates)
    age_minutes = (datetime.now() - latest_dt).total_seconds() / 60

    if age_minutes > SNAPSHOT_MAX_AGE_MINUTES:
        return False, (
            f'most recent snapshot ({latest_name} in {latest_dir.name}) is '
            f'{age_minutes:.0f} min old (max: {SNAPSHOT_MAX_AGE_MINUTES} min).\n\n'
            f'  Take a fresh snapshot first:\n'
            f'    sudo systemctl start tgw-snapshot.service\n\n'
            f'  Then re-run this script.'
        )

    return True, f'snapshot {latest_name} ({latest_dir.name}) is {age_minutes:.1f} min old — OK'


def find_affected_skus() -> list[str]:
    """Return sorted list of SKUs that have a misnamed <SKU>-alt.jpg."""
    result = []
    for item_dir in sorted(ITEMDATA_ROOT.iterdir()):
        if not item_dir.is_dir():
            continue
        sku = item_dir.name
        if (item_dir / f'{sku}-alt.jpg').exists():
            result.append(sku)
    return result


# Accepted original-photo stem patterns:
#   anything ending in YYYYMMDD_HHMMSS — covers tgw*, a11b*, IMG_*, cropped-*, etc.
#   \d+                                — numbered multi-shot intake (1.jpg, 2.jpg, ...)
_OLD_FORMAT_RE = re.compile(r'^(?:.*\d{8}_\d{6}|\d+)$')


def find_original_photo(item_dir: Path, sku: str) -> tuple[Path | None, str | None]:
    """
    Find the original photo that the <SKU>-alt.jpg was renamed from.

    Looks for a .jpg in item_dir that:
      - is not named <SKU>-alt.jpg (the misnamed file itself)
      - is not named <SKU>.jpg    (wrong file from a previous repair attempt)
      - is not any *-alt.jpg      (not another alt companion)
      - has the same file size as <SKU>-alt.jpg
      - has the expected old-format name (tgwYYYYMMDD_HHMMSS or a11bYYYYMMDD_HHMMSS)

    Returns (path, None) on success, (None, reason) if something is off.
    """
    alt_path = item_dir / f'{sku}-alt.jpg'
    alt_size = alt_path.stat().st_size

    candidates = []
    for p in item_dir.iterdir():
        if p.suffix.lower() != '.jpg':
            continue
        if p.name == f'{sku}-alt.jpg':
            continue
        if p.name == f'{sku}.jpg':
            continue
        if p.name.endswith('-alt.jpg'):
            continue
        if p.stat().st_size == alt_size:
            candidates.append(p)

    if len(candidates) == 0:
        return None, 'no size-matching original photo found'
    if len(candidates) > 1:
        names = ', '.join(p.name for p in candidates)
        return None, f'multiple size-matching originals (ambiguous): {names}'

    original = candidates[0]
    if not _OLD_FORMAT_RE.match(original.stem):
        return None, (
            f'matched original ({original.name}) does not have expected old-format name '
            f'(tgwYYYYMMDD_HHMMSS / a11bYYYYMMDD_HHMMSS) — manual review needed'
        )

    return original, None


def repair_item(sku: str, execute: bool) -> dict:
    """
    Rename <SKU>-alt.jpg → <original>-alt.jpg for one item.

    Returns a result dict with keys: status, reason, original, new_alt.
    status values: RENAMED | DRY_RUN | SKIP | ERROR
    """
    r: dict = {
        'sku': sku,
        'original': None,
        'new_alt': None,
        'status': 'ERROR',
        'reason': None,
    }

    item_dir = ITEMDATA_ROOT / sku
    alt_path = item_dir / f'{sku}-alt.jpg'

    if not alt_path.exists():
        r['reason'] = f'{sku}-alt.jpg not found (already repaired?)'
        r['status'] = 'SKIP'
        return r

    # Find the original photo by size match
    original, find_err = find_original_photo(item_dir, sku)
    if original is None:
        r['reason'] = find_err
        return r

    r['original'] = original.name
    new_alt = item_dir / f'{original.stem}-alt.jpg'
    r['new_alt'] = new_alt.name

    # New alt must not already exist
    if new_alt.exists():
        r['status'] = 'SKIP'
        r['reason'] = f'{new_alt.name} already exists — not overwriting'
        return r

    # Both files must be valid JPEGs
    if not is_valid_jpeg(alt_path):
        r['reason'] = f'{alt_path.name} is not a valid JPEG'
        return r
    if not is_valid_jpeg(original):
        r['reason'] = f'{original.name} is not a valid JPEG'
        return r

    # Confirm content match: first CONTENT_CHECK_BYTES of both must be identical
    alt_head = first_n_bytes(alt_path, CONTENT_CHECK_BYTES)
    orig_head = first_n_bytes(original, CONTENT_CHECK_BYTES)
    if alt_head is None or orig_head is None:
        r['reason'] = 'could not read file contents for comparison'
        return r
    if alt_head != orig_head:
        r['reason'] = (
            f'first {CONTENT_CHECK_BYTES} bytes differ between {alt_path.name} and '
            f'{original.name} — same size but different content; manual review needed'
        )
        return r

    if not execute:
        r['status'] = 'DRY_RUN'
        return r

    # Rename
    try:
        alt_path.rename(new_alt)
    except OSError as e:
        r['reason'] = f'rename failed: {e}'
        return r

    # Post-rename verification
    if alt_path.exists():
        r['reason'] = 'rename appeared to succeed but old path still exists'
        return r
    if not new_alt.exists():
        r['reason'] = 'rename appeared to succeed but new path not found'
        return r
    if new_alt.stat().st_size != original.stat().st_size:
        r['reason'] = 'renamed file size does not match original — unexpected'
        return r

    # Cleanup: remove any wrongly-created <SKU>.jpg from a prior repair attempt
    wrong_primary = item_dir / f'{sku}.jpg'
    if wrong_primary.exists():
        wrong_size = wrong_primary.stat().st_size
        if wrong_size == original.stat().st_size:
            try:
                wrong_primary.unlink()
                log.info('[CLEANUP] %s | removed wrongly-created %s', sku, wrong_primary.name)
            except OSError as e:
                log.warning('[CLEANUP] %s | could not remove %s: %s', sku, wrong_primary.name, e)
        else:
            log.warning(
                '[CLEANUP] %s | %s exists but size (%d) differs from original (%d) — left in place',
                sku, wrong_primary.name, wrong_size, original.stat().st_size,
            )

    r['status'] = 'RENAMED'
    return r


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Repair ISS-013: rename misnamed <SKU>-alt.jpg to <original>-alt.jpg.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Run --dry-run first. Review the output. Then run --execute.',
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true',
                      help='Show what would happen without renaming any files')
    mode.add_argument('--execute', action='store_true',
                      help='Perform renames (requires a fresh Btrfs snapshot)')
    parser.add_argument('--sku', metavar='SKU',
                        help='Limit to a single SKU (use for spot-checking)')
    parser.add_argument('--skip-snapshot-check', action='store_true',
                        help='Skip the snapshot age check')
    args = parser.parse_args()

    execute: bool = args.execute

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f'photo_repair_iss013_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
    fh = logging.FileHandler(log_path)
    fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    log.addHandler(fh)
    log.addHandler(sh)
    log.setLevel(logging.INFO)

    mode_label = 'EXECUTE — files will be renamed' if execute else 'DRY-RUN — no files changed'
    log.info('=== photo_repair_iss013 | %s ===', mode_label)
    log.info('Log: %s', log_path)

    if execute and not args.skip_snapshot_check:
        snap_ok, snap_msg = check_recent_snapshot()
        if snap_ok:
            log.info('Snapshot check: %s', snap_msg)
        else:
            log.error('Snapshot check FAILED: %s', snap_msg)
            return 1

    skus = find_affected_skus()

    if args.sku:
        skus = [s for s in skus if s == args.sku]
        if not skus:
            log.error('SKU %s not found in affected set', args.sku)
            return 1

    log.info('Found %d affected items', len(skus))

    counts: dict[str, list] = {'RENAMED': [], 'DRY_RUN': [], 'SKIP': [], 'ERROR': []}

    for sku in skus:
        r = repair_item(sku, execute)
        counts[r['status']].append(r)
        if r['status'] == 'ERROR':
            log.error('[ERROR]  %s | %s', sku, r['reason'])
        elif r['status'] == 'SKIP':
            log.warning('[SKIP]   %s | %s', sku, r['reason'])
        else:
            arrow = f'{r["sku"]}-alt.jpg → {r["new_alt"]}' if r['new_alt'] else sku
            log.info('[%s] %s', r['status'], arrow)

    renamed  = len(counts['RENAMED'])
    dry      = len(counts['DRY_RUN'])
    skipped  = len(counts['SKIP'])
    errors   = len(counts['ERROR'])

    log.info('')
    log.info('=== SUMMARY ===')
    if execute:
        log.info('Renamed:  %d', renamed)
    else:
        log.info('Would rename: %d  (re-run with --execute to apply)', dry)
    log.info('Skipped:  %d', skipped)
    log.info('Errors:   %d', errors)
    if counts['ERROR']:
        log.error('Items requiring manual review:')
        for r in counts['ERROR']:
            log.error('  %s: %s', r['sku'], r['reason'])
    log.info('Full log: %s', log_path)

    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
