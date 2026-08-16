#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""check_review_md.py — mechanical pre-stitch gate: confirm a todo's
-REVIEW.md exists before its branch is stitched.

Root cause (todo #1366, PP-HERMES-EA-001): `tgw-runner-review`'s mandated
an immutable governed-review evidence root write (SKILL.md
"Clean path — hand off to stitch" step) got silently skipped for 6 of 7
concurrent-batch-stitched todos in one session (#1280/#1282/#1284/#1288/
#1291/#1297), discovered and backfilled only after the fact
(2026-07-13). Nothing mechanical caught the omission before merge. This
script is that mechanical catch: given one or more todo ids, it checks
that a `-REVIEW.md` file for each exists under the configured external
review-evidence root, and exits non-zero (with a
clear per-id report) if any are missing.

This tool does NOT judge review *content* — that's tgw-runner-review's
job. It only confirms the artifact exists, which is exactly the class of
omission that slipped through undetected.

Usage:
    # Single todo id
    python3 scripts/check_review_md.py 1280

    # Multiple ids (e.g. one concurrent-batch wave)
    python3 scripts/check_review_md.py 1280 1282 1284 1288 1291 1297

    # Scan every branch currently pending stitch (todo/<id>-<slug> branches
    # that exist locally) and check each one's id
    python3 scripts/check_review_md.py --scan-branches

Exit code: 0 if every checked id has a -REVIEW.md, 1 if any is missing.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List

_DEFAULT_RESULTS_DIR = Path('/opt/TGW/var/governed-review-evidence')
RESULTS_DIR = Path(os.environ.get('TGW_REVIEW_RESULTS_DIR', _DEFAULT_RESULTS_DIR))

# A -REVIEW.md filename may cover one id ("<id>-REVIEW.md") or a
# hyphenated multi-id/slug batch ("<id>-<id2>-slug-REVIEW.md" or
# "<id>-slug-REVIEW.md") — match the todo id anywhere in the leading
# hyphen-separated numeric run of the filename.
_LEADING_ID_RUN = re.compile(r'^(\d+(?:-\d+)*)-')


def find_review_md(todo_id: str) -> Path | None:
    """Return the -REVIEW.md path covering todo_id, or None if missing."""
    if not RESULTS_DIR.is_dir():
        return None
    for candidate in RESULTS_DIR.glob('*-REVIEW.md'):
        m = _LEADING_ID_RUN.match(candidate.name)
        if not m:
            continue
        ids_in_name = m.group(1).split('-')
        if todo_id in ids_in_name:
            return candidate
    return None


def check_ids(todo_ids: List[str]) -> int:
    """Print a per-id report; return process exit code (0 clean, 1 fail)."""
    missing = []
    for todo_id in todo_ids:
        found = find_review_md(todo_id)
        if found is not None:
            print(f'OK   #{todo_id}: {found.relative_to(RESULTS_DIR)}')
        else:
            missing.append(todo_id)
            print(f'MISS #{todo_id}: no -REVIEW.md found under {RESULTS_DIR}')

    if missing:
        print(
            f'\nBLOCKED: {len(missing)} of {len(todo_ids)} todo(s) missing '
            f'-REVIEW.md, do not stitch: {", ".join(missing)}',
            file=sys.stderr,
        )
        return 1

    print(f'\nCLEAR: all {len(todo_ids)} todo(s) have a -REVIEW.md, safe to stitch.')
    return 0


def _discover_branch_ids() -> List[str]:
    """Extract todo ids from local todo/<id>-<slug> branches."""
    try:
        out = subprocess.run(
            ['git', 'branch', '--list', 'todo/*'],
            check=True, capture_output=True, text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f'error: could not list git branches: {exc}', file=sys.stderr)
        return []

    ids = []
    for line in out.splitlines():
        name = line.strip().lstrip('* ').strip()
        m = re.match(r'^todo/(\d+(?:-\d+)*)-', name)
        if m:
            ids.extend(m.group(1).split('-'))
    return sorted(set(ids), key=int)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('todo_ids', nargs='*', help='Todo id(s) to check')
    parser.add_argument(
        '--scan-branches', action='store_true',
        help='Derive todo ids from local todo/<id>-<slug> branches instead of args',
    )
    parser.add_argument(
        '--results-dir', type=Path,
        help='external immutable review-evidence root (defaults to TGW_REVIEW_RESULTS_DIR)',
    )
    args = parser.parse_args(argv)

    if args.results_dir is not None:
        global RESULTS_DIR
        RESULTS_DIR = args.results_dir

    if args.scan_branches:
        todo_ids = _discover_branch_ids()
        if not todo_ids:
            print('No local todo/<id>-<slug> branches found.')
            return 0
    else:
        todo_ids = args.todo_ids

    if not todo_ids:
        parser.error('provide at least one todo id, or pass --scan-branches')

    return check_ids(todo_ids)


if __name__ == '__main__':
    sys.exit(main())
