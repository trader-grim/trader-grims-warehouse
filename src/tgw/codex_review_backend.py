"""Dedicated ephemeral Codex backend for isolated semantic review requests."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

Invoke = Callable[..., subprocess.CompletedProcess[str]]

REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "verdict", "snapshot_hash", "summary", "findings"],
    "properties": {
        "schema": {"const": "tgw-code-review/v1"},
        "verdict": {"enum": ["PASS", "FAIL"]},
        "snapshot_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
        "findings": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "path", "line", "message"],
                "properties": {
                    "severity": {"enum": ["critical", "high", "medium", "low"]},
                    "path": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "line": {"type": "integer", "minimum": 1},
                    "message": {"type": "string", "minLength": 1, "maxLength": 4000},
                },
            },
        },
    },
}


class CodexReviewBackendError(ValueError):
    pass


def health(
    *, codex_bin: str | Path | None = None, auth_file: str | Path | None = None
) -> dict[str, Any]:
    configured = Path(codex_bin) if codex_bin else Path(
        os.environ.get("TGW_CODEX_REVIEW_BIN", Path.home() / ".local/bin/codex")
    )
    executable = (
        configured.absolute()
        if configured.is_absolute() and configured.is_file() and os.access(configured, os.X_OK)
        else Path(shutil.which(str(configured)) or "")
    )
    auth = Path(auth_file) if auth_file else Path(
        os.environ.get("TGW_CODEX_REVIEW_AUTH", Path.home() / ".codex/auth.json")
    )
    reasons = []
    if not executable or not executable.is_file() or not os.access(executable, os.X_OK):
        reasons.append("dedicated Codex review executable is unavailable")
    if not auth.is_file():
        reasons.append("dedicated Codex review authentication is unavailable")
    return {
        "schema": "tgw-codex-review-backend-health/v1",
        "available": not reasons,
        "executable": str(executable) if executable and executable.is_file() else None,
        "auth_file": str(auth.absolute()) if auth.is_file() else None,
        "reasons": reasons,
    }


def _prompt(request: Mapping[str, Any]) -> str:
    return f"""Perform an independent semantic and security review of the immutable snapshot in the current directory.

This is review-only. Do not modify files, run network operations, deploy, commit,
or grant authority. Inspect the snapshot directly. Report every material semantic
or security defect with an exact snapshot-relative path and line. PASS means there
are no unresolved findings. Bind the report to snapshot hash:
{request['snapshot_hash']}

Return only the requested JSON report schema.
"""


def run(
    request: Mapping[str, Any],
    cwd: Path,
    *,
    codex_bin: str | Path | None = None,
    auth_file: str | Path | None = None,
    invoke: Invoke = subprocess.run,
) -> dict[str, Any]:
    if set(request) != {
        "schema",
        "handoff_hash",
        "card_hash",
        "snapshot_hash",
        "snapshot_root",
        "output_contract",
    } or request.get("schema") != "tgw-code-review-request/v1":
        raise CodexReviewBackendError("Codex review request contract is invalid")
    if Path(str(request["snapshot_root"])).resolve() != cwd.resolve():
        raise CodexReviewBackendError("Codex review request snapshot root mismatch")
    observed = health(codex_bin=codex_bin, auth_file=auth_file)
    if not observed["available"]:
        raise CodexReviewBackendError("; ".join(observed["reasons"]))
    with tempfile.TemporaryDirectory(prefix="tgw-codex-review-backend-") as temporary:
        temp = Path(temporary)
        schema_path = temp / "schema.json"
        output_path = temp / "report.json"
        codex_home = temp / "codex-home"
        codex_home.mkdir(mode=0o700)
        destination_auth = codex_home / "auth.json"
        shutil.copyfile(observed["auth_file"], destination_auth)
        destination_auth.chmod(0o600)
        schema_path.write_text(json.dumps(REPORT_SCHEMA, sort_keys=True))
        completed = invoke(
            [
                observed["executable"],
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--sandbox",
                "read-only",
                "-C",
                str(cwd),
                "--output-schema",
                str(schema_path),
                "-o",
                str(output_path),
                "-",
            ],
            cwd=cwd,
            input=_prompt(request),
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "CODEX_HOME": str(codex_home)},
        )
        if completed.returncode:
            raise CodexReviewBackendError(
                f"Codex review exited {completed.returncode}: {completed.stderr[-500:]}"
            )
        try:
            report = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodexReviewBackendError("Codex review returned invalid report JSON") from exc
    if report.get("snapshot_hash") != request["snapshot_hash"]:
        raise CodexReviewBackendError("Codex review report snapshot hash mismatch")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-codex-review-backend")
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
    except (CodexReviewBackendError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
