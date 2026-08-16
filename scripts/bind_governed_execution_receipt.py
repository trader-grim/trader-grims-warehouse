#!/usr/bin/env python3
"""Bind immutable card/resource/role evidence to one exact Git candidate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tgw.governed_execution_receipt import (
    GovernedExecutionReceiptError,
    create_candidate_governed_execution_receipt,
)
from tgw.logging import announce_script_run


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _git(repo: Path, candidate: str, suffix: str) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", f"{candidate}{suffix}"], cwd=repo, text=True
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(prog="bind-governed-execution-receipt")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--plan-commit", required=True)
    parser.add_argument("--card", type=Path, required=True)
    parser.add_argument("--resource-receipt", type=Path, required=True)
    parser.add_argument("--role-receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        repo = args.repo.resolve()
        commit = _git(repo, args.candidate, "^{commit}")
        tree = _git(repo, commit, "^{tree}")
        announce_script_run(
            "bind_governed_execution_receipt.py",
            "bind execution card, resource receipt, and governed role receipt to a closed candidate",
            candidate=commit,
            plan_commit=args.plan_commit,
        )
        receipt = create_candidate_governed_execution_receipt(
            card=_object(args.card, "execution card"),
            resource_receipt=_object(args.resource_receipt, "execution resource receipt"),
            role_receipt=_object(args.role_receipt, "governed role receipt"),
            source_commit=commit,
            source_tree=tree,
            plan_commit=args.plan_commit,
        )
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    except (GovernedExecutionReceiptError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
