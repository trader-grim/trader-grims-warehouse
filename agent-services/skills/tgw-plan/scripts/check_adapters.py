#!/usr/bin/env python3
"""Check that installed TGW planning-skill adapters match canonical content."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

FILES = ("SKILL.md", "references/plan-v2.md")


def digest(root: Path) -> str:
    value = hashlib.sha256()
    for relative in FILES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(str(path))
        value.update(relative.encode())
        value.update(b"\0")
        value.update(path.read_bytes())
        value.update(b"\0")
    return value.hexdigest()


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: check_adapters.py CANONICAL_SKILL ADAPTER...")
    canonical = Path(sys.argv[1]).resolve()
    expected = digest(canonical)
    results = []
    ok = True
    for argument in sys.argv[2:]:
        adapter = Path(argument)
        try:
            observed = digest(adapter.resolve())
            match = observed == expected
            error = None
        except (OSError, FileNotFoundError) as exc:
            observed = None
            match = False
            error = f"{type(exc).__name__}: {exc}"
        ok = ok and match
        results.append({
            "path": str(adapter),
            "resolved": str(adapter.resolve(strict=False)),
            "digest": observed,
            "matches": match,
            "error": error,
        })
    print(json.dumps({
        "schema": "tgw-skill-adapter-check/v1",
        "canonical": str(canonical),
        "canonical_digest": expected,
        "adapters": results,
        "ok": ok,
    }, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
