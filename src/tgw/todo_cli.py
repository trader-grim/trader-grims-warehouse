"""Local ordinary-user CLI adapter for the existing :mod:`tgw.todo` store."""

from __future__ import annotations

import argparse
from pathlib import Path

from tgw import todo
from tgw.development.local_workflow import (
    DEFAULT_CONFIG,
    LocalCodingWorkflowError,
    load_config,
    require_coder_account,
)
from tgw.queue import state_machine


def _initialize(config_path: Path | str) -> dict:
    """Prove local access and bind every Todo-side DB user to one local DSN."""
    config = load_config(config_path)
    require_coder_account()
    dsn = config["postgres_dsn"]
    todo.init(dsn)
    state_machine.init(dsn)
    return config


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="tgw todo",
        description="Manage the tgw-lib-local Todo store as an ordinary tgw-coders user.",
    )
    root.add_argument("agent", nargs="?", default=None)
    root.add_argument("brief_id", nargs="?", default=None)
    root.add_argument("--add", metavar="TEXT")
    root.add_argument("--done", metavar="ID", type=int)
    root.add_argument("--priority", type=int, default=50, metavar="N")
    root.add_argument("--source", default="session", metavar="SRC")
    root.add_argument("--all", dest="show_all", action="store_true")
    root.add_argument("--by-pp", dest="by_pp", action="store_true")
    root.add_argument("--seed", action="store_true")
    root.add_argument("--update", nargs="+", metavar=("ID", "TEXT"))
    root.add_argument("--note", nargs="+", metavar=("ID", "TEXT"))
    root.add_argument("--delegate", nargs=2, metavar=("ID", "AGENT"))
    root.add_argument("--set-priority", nargs=2, metavar=("ID", "N"), dest="set_priority")
    root.add_argument("--pp", default=None, metavar="PP-REF")
    root.add_argument("--depends", default=None, metavar="IDS")
    root.add_argument("--anchor", default=None, metavar="HEADING")
    root.add_argument("--set-meta", type=int, default=None, metavar="ID", dest="set_meta")
    root.add_argument("--status-note", default=None, metavar="TEXT")
    root.add_argument("--reasoning", choices=("high", "normal", "low"), default="normal")
    root.add_argument("--clip", action="store_true")
    root.add_argument("--next", action="store_true", dest="next_task")
    root.add_argument("--nextloop", action="store_true")
    root.add_argument("--agent", default=None, metavar="AGENT", dest="next_agent")
    return root


def run(
    args: argparse.Namespace,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
) -> int:
    try:
        config = _initialize(config_path)
        result = todo.cmd_todo(config, args)
        return 0 if result.get("ok", True) else 1
    except (LocalCodingWorkflowError, OSError, ValueError) as exc:
        print(f"tgw todo: {exc}", file=__import__("sys").stderr)
        return 1


def main() -> int:
    return run(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
