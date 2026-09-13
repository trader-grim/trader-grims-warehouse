"""Demo entrypoint for the todo-2017 NL-driven dotfile agent (experimental).

Takes a free-text request and a target file, shows the current content,
calls ``handle_request`` (real model proposal via opencode-then-claude),
shows a unified diff of what changed, prints the snapshot id, and offers
``--rollback <snapshot_id>``.

This is the ONE place where a real path is the default: ``--home`` and
``--allowlist-root`` default to the real ``$HOME`` when run directly.
Everything else parameterizes them so tests never touch host files.

Usage:
    python scripts/config_nl_agent_demo.py --target ~/.bashrc "add an alias ll for ls -la"
    python scripts/config_nl_agent_demo.py --rollback <snapshot_id>
"""

from __future__ import annotations

import argparse
import difflib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tgw.development import config_agent  # noqa: E402
from tgw.development import config_nl_agent  # noqa: E402


def _show_diff(before: bytes, after: bytes, name: str) -> None:
    a = before.decode("utf-8", errors="replace").splitlines(keepends=True)
    b = after.decode("utf-8", errors="replace").splitlines(keepends=True)
    diff = "".join(difflib.unified_diff(a, b, fromfile=f"a/{name}", tofile=f"b/{name}"))
    print(diff if diff else "(no changes proposed)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", nargs="?", default=None, help="free-text change request")
    parser.add_argument("--target", default=None, help="target file to change")
    parser.add_argument("--home", default=os.environ.get("HOME", str(Path.home())), help="home directory (default: real $HOME)")
    parser.add_argument("--allowlist-root", default=None, help="allowlist root for config_agent (default: same as --home)")
    parser.add_argument("--rollback", default=None, metavar="SNAPSHOT_ID", help="restore a snapshot instead of applying a change")
    args = parser.parse_args()

    home = Path(args.home)
    allowlist_root = Path(args.allowlist_root) if args.allowlist_root else home

    if args.rollback:
        config_agent.rollback(args.rollback)
        print(f"rolled back to snapshot {args.rollback}")
        return 0

    if not args.request or not args.target:
        parser.error("need a REQUEST and --target (or use --rollback SNAPSHOT_ID)")
    target = Path(args.target).expanduser()

    print(f"home:           {home}")
    print(f"allowlist root: {allowlist_root}")
    print(f"target:         {target}")
    print(f"allowed:        {config_nl_agent.is_allowed_target(target, home)}")
    print(f"request:        {args.request}")

    before = target.read_bytes() if target.is_file() else b""
    print("----- BEGIN CURRENT CONTENT -----")
    print(before.decode("utf-8", errors="replace"), end="" if before.endswith(b"\n") or not before else "\n")
    print("----- END CURRENT CONTENT -----")

    snapshot_id = config_nl_agent.handle_request(
        args.request, target, home=home, allowlist_root=allowlist_root
    )
    after = target.read_bytes()
    print("----- BEGIN DIFF -----")
    _show_diff(before, after, target.name)
    print("----- END DIFF -----")
    print(f"snapshot: {snapshot_id}")
    print(f"restore with: python scripts/config_nl_agent_demo.py --rollback {snapshot_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
