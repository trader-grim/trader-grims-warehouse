from __future__ import annotations

import argparse
import json
import sys

from .handoff import HandoffError, craft_handoff, verify_for_launcher


def main() -> int:
    parser = argparse.ArgumentParser(prog="promptcraft-handoff")
    parser.add_argument("operation", choices=("craft", "verify"))
    parser.add_argument("--receiver-identity")
    args = parser.parse_args()
    try:
        value = json.load(sys.stdin)
        if args.operation == "craft":
            if not args.receiver_identity:
                raise HandoffError("--receiver-identity is required for craft")
            result = craft_handoff(value, receiver_identity=args.receiver_identity)
        else:
            result = verify_for_launcher(value)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (HandoffError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
