"""Public, local-only Plan taskboard command for tgw-lib operators."""

from __future__ import annotations

import argparse
from pathlib import Path

from tgw.config import load_operational_config
from tgw.plan_render import (
    format_plan_check,
    format_plan_status,
    plan_check,
    plan_status,
    render_taskboard,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tgw plan")
    parser.add_argument("--config", type=Path, required=True, help=argparse.SUPPRESS)
    parser.add_argument("operation", choices=("render", "check", "status"))
    parser.add_argument("--pp", dest="pp_ref", metavar="PP_REF")
    return parser


def main() -> int:
    args = _parser().parse_args()
    config = load_operational_config(args.config)
    if args.operation == "render":
        result = render_taskboard(config)
        if result.get("ok"):
            identity = result["plan_identity"]
            print(
                f"Taskboard rendered: {result['path']} "
                f"({result['open']} open, {result['done_week']} done this week)\n"
                f"Bound Plan: {identity['plan_commit']} {identity['solution_hash']}"
            )
        else:
            print(f"Error: {result.get('error')}")
    elif args.operation == "check":
        result = plan_check(config)
        print(format_plan_check(result))
    else:
        result = plan_status(config, pp_ref=args.pp_ref)
        print(format_plan_status(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
