"""Dedicated ephemeral Claude Code backend for isolated semantic review requests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.codex_review_backend import REPORT_SCHEMA
from tgw.workers.codex_implement import _claude_report

Invoke = Callable[..., subprocess.CompletedProcess[str]]


class ClaudeReviewBackendError(ValueError):
    pass


def health(*, claude_bin: str | Path | None = None) -> dict[str, Any]:
    configured = Path(claude_bin) if claude_bin else Path(
        os.environ.get("TGW_CLAUDE_REVIEW_BIN", Path.home() / ".local/bin/claude")
    )
    executable = (
        configured.absolute()
        if configured.is_absolute() and configured.is_file() and os.access(configured, os.X_OK)
        else Path(shutil.which(str(configured)) or "")
    )
    reasons = []
    if not executable or not executable.is_file() or not os.access(executable, os.X_OK):
        reasons.append("dedicated Claude review executable is unavailable")
    return {
        "schema": "tgw-claude-review-backend-health/v1",
        "available": not reasons,
        "executable": str(executable) if executable and executable.is_file() else None,
        "reasons": reasons,
    }


def _prompt(request: Mapping[str, Any]) -> str:
    context = request.get("review_context")
    context_text = (
        json.dumps(context, sort_keys=True, indent=2)
        if isinstance(context, Mapping)
        else "No additional bounded task context was supplied."
    )
    return f"""Perform an independent semantic and security review of the immutable snapshot in the current directory.

This is review-only. Do not modify files, run network operations, deploy, commit,
or grant authority. Inspect the snapshot directly. Report every material semantic
or security defect against the bounded task intent and acceptance conditions below,
with an exact snapshot-relative path and line. PASS means there are no unresolved
findings. Bind the report to snapshot hash:
{request['snapshot_hash']}

Bound task and candidate context:
{context_text}

Return only the requested JSON report object, as the very last thing you output, with
no markdown fence, prose, or punctuation before or after it. It MUST match this exact
JSON schema:
{json.dumps(REPORT_SCHEMA, sort_keys=True)}
"""


def run(
    request: Mapping[str, Any],
    cwd: Path,
    *,
    claude_bin: str | Path | None = None,
    invoke: Invoke = subprocess.run,
) -> dict[str, Any]:
    required = {
        "schema",
        "handoff_hash",
        "card_hash",
        "snapshot_hash",
        "snapshot_root",
        "output_contract",
    }
    allowed_fields = {frozenset(required), frozenset({*required, "review_context"})}
    if (
        frozenset(request) not in allowed_fields
        or request.get("schema") != "tgw-code-review-request/v1"
    ):
        raise ClaudeReviewBackendError("Claude review request contract is invalid")
    context = request.get("review_context")
    if context is not None:
        if not isinstance(context, Mapping):
            raise ClaudeReviewBackendError("Claude review context is invalid")
        unsigned_context = dict(context)
        claimed_context = unsigned_context.pop("context_hash", None)
        canonical = json.dumps(
            unsigned_context,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        if claimed_context != "sha256:" + hashlib.sha256(canonical).hexdigest():
            raise ClaudeReviewBackendError("Claude review context hash is invalid")
    if Path(str(request["snapshot_root"])).resolve() != cwd.resolve():
        raise ClaudeReviewBackendError("Claude review request snapshot root mismatch")
    observed = health(claude_bin=claude_bin)
    if not observed["available"]:
        raise ClaudeReviewBackendError("; ".join(observed["reasons"]))
    completed = invoke(
        [
            observed["executable"],
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            "bypassPermissions",
        ],
        cwd=cwd,
        input=_prompt(request),
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ},
    )
    if completed.returncode:
        raise ClaudeReviewBackendError(
            f"Claude review exited {completed.returncode}: {completed.stderr[-500:]}"
        )
    report = _claude_report(completed.stdout)
    if report is None:
        raise ClaudeReviewBackendError("Claude review returned invalid report JSON")
    if report.get("snapshot_hash") != request["snapshot_hash"]:
        raise ClaudeReviewBackendError("Claude review report snapshot hash mismatch")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-claude-review-backend")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.health:
        observed = health()
        print(json.dumps(observed, sort_keys=True))
        return 0 if observed["available"] else 2
    try:
        result = run(json.load(sys.stdin), Path.cwd())
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ClaudeReviewBackendError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
