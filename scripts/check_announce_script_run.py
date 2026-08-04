#!/usr/bin/env python3
"""check_announce_script_run.py — invariant E9 detector (todo #1250).

Every one-off script under scripts/*.py that defines a standalone
entrypoint (`def main(`) is expected to call
`tgw.logging.announce_script_run()` before doing any real work — see
CLAUDE.md's "one-off scripts announce themselves" rule and the 2026-07-04/05
requeue-storm incident that motivated it (a script ran with zero durable
trace that it had run at all).

This is a plain grep-based check, deliberately not an AST walk: it flags any
scripts/*.py file that contains a `def main(` definition but no
`announce_script_run(` call anywhere in the file. That is a conservative
(cheap, no false negatives on the common case) check, not a guarantee the
call is reachable from every code path — a script that calls it from a
branch that never executes would still pass. Good enough for a fleet-wide
sweep; not a substitute for review.

Exit code: 0 if every scripts/*.py with a main() also announces; 1 and a
listing of offenders otherwise.

Usage:
    python3 scripts/check_announce_script_run.py
    python3 scripts/check_announce_script_run.py --scripts-dir /path/to/scripts
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List

_MAIN_RE = re.compile(r'^def main\s*\(', re.MULTILINE)
_ANNOUNCE_RE = re.compile(r'announce_script_run\s*\(')

# Scripts that legitimately have a main() but are exempt from the
# announce-yourself rule (e.g. the detector script itself, or a script that
# is imported-only / never run standalone). Keep this list short and named —
# an entry here should be defensible on its own, not a way to silence the
# detector.
_EXEMPT = {
    'check_announce_script_run.py',  # the detector itself
    'check_review_md.py',  # pre-stitch gate (todo #1366), a read-only checker, not a data-mutating one-off
}


def find_offenders(scripts_dir: Path) -> List[Path]:
    offenders = []
    for path in sorted(scripts_dir.glob('*.py')):
        if path.name in _EXEMPT:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue
        if _MAIN_RE.search(text) and not _ANNOUNCE_RE.search(text):
            offenders.append(path)
    return offenders


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--scripts-dir', default=None,
                     help='Directory to scan (default: scripts/ next to this file\'s repo root)')
    args = ap.parse_args()

    if args.scripts_dir:
        scripts_dir = Path(args.scripts_dir)
    else:
        scripts_dir = Path(__file__).resolve().parent

    offenders = find_offenders(scripts_dir)

    if offenders:
        print('invariant E9 violation: script(s) with main() but no announce_script_run() call:')
        for path in offenders:
            print(f'  {path}')
        print('\nFix: call tgw.logging.announce_script_run() near the top of main(), '
              'before touching the queue or any data. See CLAUDE.md '
              '"one-off scripts announce themselves" / invariant E9.')
        return 1

    print(f'OK: every scripts/*.py with a main() in {scripts_dir} calls announce_script_run().')
    return 0


if __name__ == '__main__':
    sys.exit(main())
