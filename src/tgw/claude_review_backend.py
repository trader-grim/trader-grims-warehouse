"""Dedicated ephemeral Claude Code backend for isolated semantic review requests."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.codex_review_backend import REPORT_SCHEMA
from tgw.workers.codex_implement import _claude_report

Invoke = Callable[..., subprocess.CompletedProcess[str]]

log = logging.getLogger(__name__)


class ClaudeReviewBackendError(ValueError):
    """A Claude review invocation or its report violated the governed contract.

    ``raw_report`` and ``raw_stdout`` carry the model's own output when the
    failure is an output-dialect defect (unparseable report, noncanonical
    finding shapes).  Callers persist them in the failure artifact so the
    dialect deviation is diagnosable without a manual repro.
    """

    def __init__(
        self,
        *args: Any,
        raw_report: Any = None,
        raw_stdout: str | None = None,
    ) -> None:
        super().__init__(*args)
        self.raw_report = raw_report
        self.raw_stdout = raw_stdout


_FINDING_MESSAGE_KEYS = (
    "message",
    "description",
    "detail",
    "details",
    "summary",
    "text",
    "body",
)
_FINDING_PATH_KEYS = ("path", "file", "file_path", "filename", "location")
_FINDING_LINE_KEYS = (
    "line",
    "line_number",
    "lineno",
    "start_line",
    "line_start",
    "begin_line",
)
_FINDING_SEVERITY_KEYS = ("severity", "level", "priority", "impact")
_SEVERITY_ALIASES = {
    "critical": "critical",
    "blocker": "critical",
    "fatal": "critical",
    "high": "high",
    "error": "high",
    "major": "high",
    "severe": "high",
    "medium": "medium",
    "moderate": "medium",
    "warning": "medium",
    "warn": "medium",
    "normal": "medium",
    "low": "low",
    "minor": "low",
    "info": "low",
    "informational": "low",
    "note": "low",
    "nit": "low",
    "trivial": "low",
    "style": "low",
}


def _first_present(finding: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in finding and finding[key] not in (None, ""):
            return finding[key]
    return None


def _normalize_finding(
    finding: Any, index: int, snapshot_root: Path
) -> dict[str, Any]:
    """Map a model-variant finding shape onto the exact review-contract fields.

    Tolerated deviations: unknown extra keys (dropped), ``description`` /
    ``detail`` in place of ``message``, ``file`` in place of ``path``, a
    missing or zero ``line`` (clamped to 1), a stringified line number, and
    severity aliases / casing.  Anything that cannot be mapped -- a finding
    that is not an object, carries no message text or path, or has an
    unmappable severity or non-integer line -- raises a structured error that
    names the deviation.

    The normalized ``path`` and ``line`` are additionally checked against the
    immutable snapshot at ``snapshot_root`` (the review cwd): a path that is
    absolute or escapes the snapshot, that names a file absent from the
    snapshot, or a line past the end of that file raises a structured error.
    The provider-neutral contract validator applied downstream enforces the
    same three conditions; catching them here keeps the deviation attributable
    to the model's own output (raw report + stdout) instead of surfacing later
    as a bare ``ReviewRunnerError`` with no report evidence.
    """
    if not isinstance(finding, Mapping):
        raise ClaudeReviewBackendError(
            f"Claude review finding at index {index} is not a JSON object"
        )
    message = _first_present(finding, _FINDING_MESSAGE_KEYS)
    if not isinstance(message, str) or not message.strip():
        raise ClaudeReviewBackendError(
            f"Claude review finding at index {index} has no message/description text"
        )
    path = _first_present(finding, _FINDING_PATH_KEYS)
    if not isinstance(path, str) or not path.strip():
        raise ClaudeReviewBackendError(
            f"Claude review finding at index {index} has no snapshot-relative path"
        )
    severity_raw = _first_present(finding, _FINDING_SEVERITY_KEYS)
    if severity_raw is None:
        severity = "medium"
    elif (
        isinstance(severity_raw, str)
        and severity_raw.strip().lower() in _SEVERITY_ALIASES
    ):
        severity = _SEVERITY_ALIASES[severity_raw.strip().lower()]
    else:
        raise ClaudeReviewBackendError(
            f"Claude review finding at index {index} has an unmappable severity "
            f"{severity_raw!r}"
        )
    line_raw = _first_present(finding, _FINDING_LINE_KEYS)
    if line_raw is None or isinstance(line_raw, bool):
        line = 1
    else:
        try:
            line = int(str(line_raw).strip())
        except (TypeError, ValueError):
            raise ClaudeReviewBackendError(
                f"Claude review finding at index {index} has a non-integer line "
                f"{line_raw!r}"
            ) from None
        if line < 1:
            line = 1
    path = path.strip()
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ClaudeReviewBackendError(
            f"Claude review finding at index {index} path {path!r} is not "
            "snapshot-relative"
        )
    source = snapshot_root / relative
    if not source.is_file():
        raise ClaudeReviewBackendError(
            f"Claude review finding at index {index} path {path!r} is absent "
            "from the snapshot"
        )
    source_lines = len(
        source.read_text(encoding="utf-8", errors="replace").splitlines()
    )
    if line > source_lines:
        raise ClaudeReviewBackendError(
            f"Claude review finding at index {index} line {line} is outside "
            f"{path!r} ({source_lines} line(s) in the snapshot)"
        )
    return {
        "severity": severity,
        "path": path,
        "line": line,
        "message": message.strip(),
    }


def _normalize_report(report: Any, snapshot_root: Path) -> dict[str, Any]:
    """Coerce a model-variant review report onto ``tgw-code-review/v1``.

    Findings are normalized field-by-field and checked against the immutable
    snapshot at ``snapshot_root`` (see :func:`_normalize_finding`).
    The verdict is derived from whether any findings survived normalization --
    the only self-consistent shapes the contract admits are ``PASS`` with no
    findings and ``FAIL`` with at least one -- so a model that names the
    wrong verdict but cites its findings still yields a valid receipt.  A
    ``FAIL`` verdict with no findings is genuinely ambiguous and raises a
    structured error instead of being silently downgraded to ``PASS``.
    """
    if not isinstance(report, Mapping):
        raise ClaudeReviewBackendError("Claude review report is not a JSON object")
    schema = report.get("schema")
    if schema not in (None, "", "tgw-code-review/v1"):
        raise ClaudeReviewBackendError(
            f"Claude review report schema {schema!r} is not tgw-code-review/v1"
        )
    findings_raw = report.get("findings")
    if findings_raw is None:
        findings_raw = []
    if not isinstance(findings_raw, list):
        raise ClaudeReviewBackendError("Claude review findings are not a list")
    findings = [
        _normalize_finding(item, index, snapshot_root)
        for index, item in enumerate(findings_raw)
    ]
    model_verdict = report.get("verdict")
    if isinstance(model_verdict, str):
        model_verdict = model_verdict.strip().upper()
    if model_verdict == "FAIL" and not findings:
        raise ClaudeReviewBackendError(
            "Claude review returned verdict FAIL with no findings; the diagnostic "
            "verdict cannot be cited to a snapshot line"
        )
    verdict = "FAIL" if findings else "PASS"
    if model_verdict in {"PASS", "FAIL"} and model_verdict != verdict:
        log.warning(
            "Claude review model verdict %r disagrees with the finding-derived "
            "verdict %r (findings=%d); using the finding-derived verdict",
            model_verdict,
            verdict,
            len(findings),
        )
    summary = report.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        alternate = report.get("description") or report.get("overview")
        if isinstance(alternate, str) and alternate.strip():
            summary = alternate.strip()
        elif findings:
            summary = "; ".join(item["message"] for item in findings)[:4000]
        else:
            summary = "No material semantic or security findings."
    return {
        "schema": "tgw-code-review/v1",
        "verdict": verdict,
        "snapshot_hash": report.get("snapshot_hash"),
        "summary": summary[:4000],
        "findings": findings,
    }


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
            f"Claude review exited {completed.returncode}: {completed.stderr[-500:]}",
            raw_stdout=completed.stdout,
        )
    raw_stdout = completed.stdout
    raw_report = _claude_report(raw_stdout)
    if raw_report is None:
        raise ClaudeReviewBackendError(
            "Claude review returned invalid report JSON", raw_stdout=raw_stdout
        )
    try:
        report = _normalize_report(raw_report, cwd)
    except ClaudeReviewBackendError as exc:
        exc.raw_report = raw_report
        exc.raw_stdout = raw_stdout
        raise
    if report.get("snapshot_hash") != request["snapshot_hash"]:
        log.warning(
            "Claude review echoed snapshot_hash %r instead of the bound request "
            "snapshot_hash %r; stamping the request's snapshot_hash since it is "
            "request identity, not model output",
            report.get("snapshot_hash"),
            request["snapshot_hash"],
        )
    report = {**report, "snapshot_hash": request["snapshot_hash"]}
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
        payload: dict[str, Any] = {"error": str(exc), "error_type": type(exc).__name__}
        raw_report = getattr(exc, "raw_report", None)
        if raw_report is not None:
            payload["raw_report"] = raw_report
        raw_stdout = getattr(exc, "raw_stdout", None)
        if isinstance(raw_stdout, str) and raw_stdout:
            payload["raw_stdout"] = raw_stdout[-8000:]
        print(json.dumps(payload, default=repr), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
