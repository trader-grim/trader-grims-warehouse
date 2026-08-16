#!/usr/bin/env python3
"""Run the fail-closed, receipt-sink-backed governed candidate admission gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tgw.candidate_receipt_sink import (
    CandidateReceiptSinkError,
    PinnedGitReceiptSink,
    candidate_admission_gate,
    load_receipt_sink_descriptor,
)
from tgw.logging import announce_script_run


def main() -> int:
    parser = argparse.ArgumentParser(prog="admit-governed-candidate")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--plan-commit", required=True)
    parser.add_argument(
        "--receipt-sink-config", type=Path, required=True,
        help="operator-configured pinned Git receipt-sink descriptor; candidate-local configuration is refused",
    )
    args = parser.parse_args()
    try:
        repository = args.repo.resolve(strict=True)
        descriptor = load_receipt_sink_descriptor(args.receipt_sink_config, candidate_repository=repository)
        sink = PinnedGitReceiptSink(descriptor, candidate_repository=repository)
        announce_script_run(
            "admit_governed_candidate.py",
            "verify governed candidate execution evidence from a configured immutable receipt sink",
            candidate=args.candidate,
            plan_commit=args.plan_commit,
        )
        gate = candidate_admission_gate(
            repository, candidate=args.candidate, plan_commit=args.plan_commit, sink=sink,
        )
        print(json.dumps(gate, sort_keys=True, separators=(",", ":")))
        return 0 if gate["allowed"] else 3
    except (CandidateReceiptSinkError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
