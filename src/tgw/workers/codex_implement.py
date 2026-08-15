"""Narrow local Codex launcher for the ``codex-implement`` treatment.

The canonical service chooses the treatment and supplies a hash-bound task
specification.  This runner gives Codex only the request worktree, then derives
the workflow outcome from Git state and a small structured final report.  It
does not commit, deploy, access production, or author workflow receipts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from tgw.errors import HardFailure

Invoke = Callable[..., subprocess.CompletedProcess[str]]

_FINAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "tests"],
    "properties": {
        "status": {"enum": ["implemented", "blocked"]},
        "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
        "tests": {
            "type": "array", "maxItems": 50,
            "items": {"type": "string", "maxLength": 1000},
        },
    },
}


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=False, text=True, capture_output=True,
    )
    if result.returncode:
        raise HardFailure(f"Codex implementation worktree Git probe failed: {result.stderr[-300:]}")
    return result.stdout.strip()


def _job_from_environment() -> dict[str, Any]:
    try:
        value = json.loads(os.environ["TGW_CODING_JOB"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise HardFailure("Codex implementation runner has no execution envelope") from exc
    if not isinstance(value, dict):
        raise HardFailure("Codex implementation execution envelope is invalid")
    return value


def _validated_task(job: dict[str, Any]) -> dict[str, Any]:
    if job.get("treatment_id") != "codex-implement" or job.get("treatment_version") != "1":
        raise HardFailure("Codex implementation runner received another treatment")
    task = job.get("task_spec")
    if (
        not isinstance(task, dict)
        or task.get("schema") != "coding-task/v1"
        or task.get("todo_id") != job.get("todo_id")
        or task.get("agent") != "codex"
        or not isinstance(task.get("body"), str)
        or not task["body"].strip()
    ):
        raise HardFailure("Codex implementation task specification is invalid")
    return task


def _codex_binary() -> str:
    configured = os.environ.get("TGW_CODEX_BIN")
    candidate = Path(configured) if configured else Path.home() / ".local/bin/codex"
    if not candidate.is_absolute() or not candidate.is_file() or not os.access(candidate, os.X_OK):
        fallback = shutil.which("codex")
        if not fallback:
            raise HardFailure("dedicated Codex executable is unavailable")
        candidate = Path(fallback)
    return str(candidate.resolve())


def _prompt(task: dict[str, Any]) -> str:
    return f"""You are the Codex implementation treatment for TGW Todo #{task['todo_id']}.

Repository AGENTS.md is your actor contract. CLAUDE.md does not govern Codex.
Work only in the current request-bound worktree. Do not commit, deploy, change
configuration or secrets, contact production, access satellite machines, import
memory, or create workflow receipt files. Implement only this bounded task and
run proportionate offline tests:

{task['body']}

Return the requested JSON report. Use status=blocked if the task cannot be
implemented inside these boundaries. The wrapper independently determines
whether source changed and does not accept your report as completion evidence.
"""


def run(job: dict[str, Any], cwd: Path, *, invoke: Invoke = subprocess.run) -> dict[str, Any]:
    task = _validated_task(job)
    before_head = _git(cwd, "rev-parse", "HEAD")
    before_status = _git(cwd, "status", "--porcelain=v1", "--untracked-files=all")
    with tempfile.TemporaryDirectory(prefix="tgw-codex-implement-") as temporary:
        temp = Path(temporary)
        schema_path, output_path = temp / "schema.json", temp / "result.json"
        codex_home = temp / "codex-home"
        codex_home.mkdir(mode=0o700)
        source_auth = Path.home() / ".codex" / "auth.json"
        if not source_auth.is_file():
            raise HardFailure("dedicated Codex authentication is unavailable")
        destination_auth = codex_home / "auth.json"
        shutil.copyfile(source_auth, destination_auth)
        destination_auth.chmod(0o600)
        schema_path.write_text(json.dumps(_FINAL_SCHEMA, sort_keys=True), encoding="utf-8")
        command = [
            _codex_binary(), "exec", "--ephemeral", "--ignore-user-config",
            # Codex 0.147 makes --approve-for-me select its workspace-write
            # sandbox and rejects an additional explicit --sandbox argument.
            "--approve-for-me", "-C", str(cwd),
            "--output-schema", str(schema_path), "-o", str(output_path), "-",
        ]
        completed = invoke(
            command, cwd=cwd, input=_prompt(task), text=True,
            capture_output=True, check=False,
            env={**os.environ, "CODEX_HOME": str(codex_home)},
        )
        if completed.returncode:
            return {
                "outcome": "failed", "established_conditions": [],
                "artifacts": [{"kind": "codex_failure", "detail": completed.stderr[-1000:]}],
            }
        try:
            report = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "outcome": "failed", "established_conditions": [],
                "artifacts": [{"kind": "codex_failure", "detail": f"invalid final report: {exc}"}],
            }

    after_head = _git(cwd, "rev-parse", "HEAD")
    after_status = _git(cwd, "status", "--porcelain=v1", "--untracked-files=all")
    if after_head != before_head:
        return {
            "outcome": "conflict", "established_conditions": [],
            "artifacts": [{"kind": "boundary_violation", "detail": "Codex changed Git HEAD"}],
        }
    changed = after_status != before_status and bool(after_status)
    valid_report = (
        isinstance(report, dict)
        and report.get("status") in {"implemented", "blocked"}
        and isinstance(report.get("summary"), str)
        and bool(report["summary"].strip())
        and isinstance(report.get("tests"), list)
        and all(isinstance(item, str) for item in report["tests"])
    )
    if not valid_report:
        return {
            "outcome": "failed", "established_conditions": [],
            "artifacts": [{"kind": "codex_failure", "detail": "final report violates runner contract"}],
        }
    artifacts = [
        {"kind": "codex_summary", "detail": report["summary"]},
        {"kind": "tests_reported", "tests": report["tests"]},
        {"kind": "git_diff", "detail": _git(cwd, "diff", "--stat")},
    ]
    if report["status"] != "implemented" or not changed:
        return {"outcome": "partial", "established_conditions": [], "artifacts": artifacts}
    return {"outcome": "satisfied", "established_conditions": ["implemented"], "artifacts": artifacts}


def main() -> int:
    try:
        result = run(_job_from_environment(), Path.cwd())
    except Exception as exc:  # runner protocol must remain structured
        result = {
            "outcome": "failed", "established_conditions": [],
            "artifacts": [{"kind": "runner_failure", "detail": str(exc)}],
        }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
