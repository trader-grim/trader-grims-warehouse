"""Command-line interface for the Plan graph pilot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .core import SourcePreconditionError, brief, build, coverage, query


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plan-graph")
    parser.add_argument("--corpus", type=Path, default=Path("corpus"))
    parser.add_argument("--allowlist", type=Path, default=Path("allowlist.txt"))
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    parser.add_argument("--source-envelope", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    q = sub.add_parser("query")
    q.add_argument("term")
    q.add_argument("--limit", type=int, default=10)
    b = sub.add_parser("brief")
    b.add_argument("task")
    b.add_argument("--limit", type=int, default=12)
    sub.add_parser("coverage")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        envelope = (
            json.loads(args.source_envelope.read_text(encoding="utf-8"))
            if args.source_envelope else None
        )
        if args.command == "build":
            result: Any = build(args.corpus, args.allowlist, args.output, source_envelope=envelope)
        elif args.command == "query":
            result = query(args.corpus, args.output, args.term, args.limit, source_envelope=envelope)
        elif args.command == "brief":
            result = brief(args.corpus, args.output, args.task, args.limit, source_envelope=envelope)
        else:
            result = coverage(args.corpus, args.allowlist, args.output, source_envelope=envelope)
    except SourcePreconditionError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "path": exc.path}}, sort_keys=True))
        return 3
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.command == "coverage" and result["stale"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
