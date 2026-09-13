"""End-to-end demo of the todo-2016 config backup/apply/rollback mechanism.

Runs the full loop against the in-repo fixture directory ONLY
(tests/fixtures/config_agent_demo/) — there is deliberately no option to
point this at any other root, let alone a real host config path:

    propose a change -> backup -> apply -> verify -> (on request) roll back

and prints the ledger trail so the mechanism is directly inspectable, not
only unit-tested. Usage:

    python scripts/config_agent_demo.py           # apply, then roll back
    python scripts/config_agent_demo.py --no-rollback   # leave change, print restore point
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tgw.development import config_agent  # noqa: E402
from tgw.development import harness_ledger  # noqa: E402

DEMO_CHANGE = b"\n# demo change applied by scripts/config_agent_demo.py (todo-2016)\n[demo]\nhello = catio\n"


def _print_ledger_trail() -> None:
    try:
        entries = harness_ledger.history(config_agent.LEDGER_TASK_ID, kinds=list(config_agent.LEDGER_KINDS))
    except Exception as exc:
        print(f"(ledger unreachable offline: {exc}; snapshot ids from disk scan)")
        for sid in config_agent.list_snapshots():
            print(f"  snapshot {sid}")
        return
    if not entries:
        print("(no config-agent ledger entries yet)")
        return
    for entry in entries:
        print(f"  seq={entry['seq']} kind={entry['kind']} body={entry['body']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-rollback", action="store_true", help="leave the demo change applied; print the restore point instead of rolling back")
    parser.add_argument("--file", default="app.conf", help="fixture file to change (basename inside the fixture dir only)")
    args = parser.parse_args()

    root = config_agent.default_allowlist_root()
    if "/" in args.file or args.file.startswith("."):
        print(f"refused: --file must be a plain basename inside {root}")
        return 2
    target = root / args.file
    print(f"allowlist root: {root}")
    print(f"target:         {target}")

    # Deliberately no allowlist override: fixture directory only in this task.
    original = target.read_bytes()
    print(f"propose: append {len(DEMO_CHANGE)} bytes to {target.name} ({len(original)} bytes now)")

    snapshot_id = config_agent.apply_change(target, original + DEMO_CHANGE, allowlist_root=root)
    print(f"backup+apply: snapshot {snapshot_id} (post-write verification: OK)")

    print(f"snapshots known: {config_agent.list_snapshots()}")

    if args.no_rollback:
        print(f"change left applied; restore with: rollback('{snapshot_id}')")
    else:
        config_agent.rollback(snapshot_id)
        current = target.read_bytes()
        print(f"rollback: restored {target.name} byte-identical: {current == original}")

    print("ledger trail:")
    _print_ledger_trail()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
