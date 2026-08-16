#!/usr/bin/env python3
"""Run the fail-closed S→D→X governed candidate admission gate.

S retains pre-execution candidate evidence (tests, migration proof, release,
and rollback).  An external exact D descriptor pins S before cards are made;
X then retains governed execution and independent-review artifacts.  Candidate
paths, a legacy one-store configuration, and static-only bindings are refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tgw.candidate_receipt_sink import (
    CandidateReceiptSinkError,
    PinnedGitReceiptSink,
    candidate_admission_gate,
    load_pinned_candidate_evidence_descriptor,
    load_receipt_sink_descriptor,
)
from tgw.logging import announce_script_run


def main() -> int:
    parser = argparse.ArgumentParser(prog="admit-governed-candidate")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument(
        "--plan-repository", type=Path, required=True,
        help="operator-configured canonical Plan repository; candidate-local Plan sources are refused",
    )
    parser.add_argument(
        "--plan-approved-ref", required=True,
        help="operator-configured immutable approved Plan ref, for example refs/tgw/approved/PLAN-ID",
    )
    parser.add_argument(
        "--candidate-evidence-descriptor-config", type=Path, required=True,
        help="operator-configured pinned Git D descriptor for exact candidate-evidence store S",
    )
    parser.add_argument(
        "--execution-evidence-sink-config", type=Path, required=True,
        help="operator-configured pinned Git execution/review store X descriptor",
    )
    args = parser.parse_args()
    try:
        repository = args.repo.resolve(strict=True)
        candidate_evidence_descriptor = load_pinned_candidate_evidence_descriptor(
            args.candidate_evidence_descriptor_config, candidate_repository=repository,
        )
        execution_descriptor = load_receipt_sink_descriptor(
            args.execution_evidence_sink_config, candidate_repository=repository,
        )
        execution_sink = PinnedGitReceiptSink(execution_descriptor, candidate_repository=repository)
        announce_script_run(
            "admit_governed_candidate.py",
            "verify acyclic candidate evidence, descriptor, governed execution, and review roots",
            candidate=args.candidate,
            plan_repository=str(args.plan_repository),
            plan_approved_ref=args.plan_approved_ref,
        )
        gate = candidate_admission_gate(
            repository, candidate=args.candidate, plan_repository=args.plan_repository,
            plan_approved_ref=args.plan_approved_ref,
            candidate_evidence_descriptor=candidate_evidence_descriptor,
            execution_sink=execution_sink,
        )
        print(json.dumps(gate, sort_keys=True, separators=(",", ":")))
        return 0 if gate["allowed"] else 3
    except (CandidateReceiptSinkError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
