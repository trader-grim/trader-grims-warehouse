from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .core import compare_prompts, compile_prompt, harness_profile, lint_prompt


def _read_text(value: str) -> str:
    if value == "-":
        return sys.stdin.read()
    return Path(value).expanduser().read_text(encoding="utf-8")


def _read_json(value: str) -> dict[str, Any]:
    return json.loads(_read_text(value))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="promptcraft")
    sub = root.add_subparsers(dest="command", required=True)

    cmd = sub.add_parser("profile")
    cmd.add_argument("harness")

    cmd = sub.add_parser("lint")
    cmd.add_argument("prompt", help="Prompt file, or - for stdin")
    cmd.add_argument("--harness", default="generic")
    cmd.add_argument("--source", action="append", default=[])
    cmd.add_argument("--tool", action="append", default=[])
    cmd.add_argument("--strict", action="store_true", help="Return nonzero for WARN as well as BLOCK")

    cmd = sub.add_parser("compile")
    cmd.add_argument("brief", help="JSON brief file, or - for stdin")
    cmd.add_argument("--prompt-only", action="store_true")

    cmd = sub.add_parser("compare")
    cmd.add_argument("old_prompt")
    cmd.add_argument("new_prompt")
    cmd.add_argument("--harness", default="generic")
    cmd.add_argument("--source", action="append", default=[])
    cmd.add_argument("--tool", action="append", default=[])
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "profile":
            result: Any = harness_profile(args.harness)
        elif args.command == "lint":
            result = lint_prompt(
                prompt=_read_text(args.prompt),
                harness=args.harness,
                source_paths=args.source,
                declared_tools=args.tool,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            if result["gate"] == "BLOCK" or (args.strict and result["gate"] == "WARN"):
                return 2
            return 0
        elif args.command == "compile":
            result = compile_prompt(_read_json(args.brief))
            if args.prompt_only:
                print(result["prompt"], end="")
                return 0
        elif args.command == "compare":
            result = compare_prompts(
                old_prompt=_read_text(args.old_prompt),
                new_prompt=_read_text(args.new_prompt),
                harness=args.harness,
                source_paths=args.source,
                declared_tools=args.tool,
            )
        else:
            raise ValueError(f"unknown command: {args.command}")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
