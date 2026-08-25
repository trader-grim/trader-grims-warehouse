"""Narrow local Codex launcher for the ``codex-implement`` treatment.

The canonical service chooses the treatment and supplies a hash-bound task
specification.  This runner gives Codex only the request worktree, then derives
the workflow outcome from Git state and a small structured final report. The
model cannot commit; the wrapper closes the implementation commit. Neither can
deploy, access production, or author workflow receipts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from tgw.development.worktree_lease import exclusive_worktree_lease as _exclusive_worktree_lease
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

_CONTEXT_MCP = Path("/opt/TGW/tgw-lib/bin/tgw-context-mcp")
_CONTEXT_TOOLS = (
    "tgw_context_code_graph",
    "tgw_context_bundle",
    "tgw_context_plan_graph",
    "tgw_context_plan_source",
    "tgw_context_current_task",
    "tgw_context_status",
    "tgw_context_onboarding",
    "tgw_context_runbooks",
)


def _write_isolated_config(codex_home: Path) -> None:
    """Expose only TGW's local read-only context MCP to the coding harness."""
    if not _CONTEXT_MCP.is_file() or not os.access(_CONTEXT_MCP, os.X_OK):
        raise HardFailure("local tgw-context MCP is unavailable")
    config = codex_home / "config.toml"
    lines = [
        "[mcp_servers.tgw-context]\n",
        f"command = {json.dumps(str(_CONTEXT_MCP))}\n",
        "args = []\n",
    ]
    for tool in _CONTEXT_TOOLS:
        lines.extend(
            (
                f"\n[mcp_servers.tgw-context.tools.{tool}]\n",
                'approval_mode = "approve"\n',
            )
        )
    config.write_text("".join(lines), encoding="utf-8")
    config.chmod(0o600)


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
        raise HardFailure("Codex implementation runner has no local job payload") from exc
    if not isinstance(value, dict):
        raise HardFailure("Codex implementation local job payload is invalid")
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
whether source changed, closes an exact commit after a successful report, and
does not accept your report as completion evidence.
"""


_RECEIPT_FILES = frozenset(
    {
        "implementation-receipt.json",
        "controller-harness-receipt.json",
        "review-receipt.json",
        "deployment-receipt.json",
        "stitch-receipt.json",
        "operator-admit-pending.json",
    }
)


def _source_status(cwd: Path) -> tuple[str, ...]:
    """Return mutable source entries while excluding workflow evidence files."""
    status = _git(
        cwd,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignored=matching",
    )
    return tuple(
        line for line in status.splitlines() if line[3:] not in _RECEIPT_FILES
    )


def _reset_index(cwd: Path) -> None:
    """Undo wrapper staging without discarding any implementation bytes."""
    completed = subprocess.run(
        ["git", "reset", "--mixed", "--quiet", "HEAD", "--"],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode:
        raise HardFailure(
            f"Codex implementation could not recover its Git index: {completed.stderr[-300:]}"
        )


def _preserve_late_source(cwd: Path, *, todo_id: int, candidate: str) -> str | None:
    """Move a lease-violating late write to Git's recovery stash, losslessly."""
    if not _source_status(cwd):
        return None
    before = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/stash"],
        cwd=cwd, check=False, text=True, capture_output=True,
    ).stdout.strip()
    pathspec = (".", *(f":(exclude){name}" for name in sorted(_RECEIPT_FILES)))
    _git(
        cwd,
        "stash",
        "push",
        "--all",
        "-m",
        f"TGW recovery Todo {todo_id} after candidate {candidate}",
        "--",
        *pathspec,
    )
    recovery = _git(cwd, "rev-parse", "--verify", "refs/stash")
    if recovery == before or _source_status(cwd):
        raise HardFailure("Codex implementation could not preserve late source cleanly")
    return recovery


def _close_candidate(
    cwd: Path, *, todo_id: int, baseline: str
) -> tuple[str, str, str | None]:
    """Commit only the implementation bytes and return the exact commit/tree."""
    pathspec = (".", *(f":(exclude){name}" for name in sorted(_RECEIPT_FILES)))
    try:
        _git(cwd, "add", "-A", "--", *pathspec)
        staged = _git(cwd, "diff", "--cached", "--name-only", "--")
        if not staged:
            raise HardFailure("Codex implementation produced no source bytes to close")
        # The lease fences cooperating TGW actors. These final checks also keep
        # a non-cooperating writer's later bytes out of the staged candidate.
        unstaged = _git(cwd, "diff", "--name-only", "--")
        untracked = tuple(
            item
            for item in _git(cwd, "ls-files", "--others", "--exclude-standard").splitlines()
            if item not in _RECEIPT_FILES
        )
        ignored = _git(
            cwd, "ls-files", "--others", "--ignored", "--exclude-standard"
        )
        if unstaged or untracked or ignored:
            raise HardFailure("Codex implementation source changed while closing its candidate")
        _git(
            cwd,
            "-c",
            "user.name=TGW Codex",
            "-c",
            "user.email=codex@tgw-lib",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "commit",
            "--no-verify",
            "-m",
            f"Todo {todo_id}: close implementation candidate",
        )
    except HardFailure:
        _reset_index(cwd)
        raise
    head = _git(cwd, "rev-parse", "HEAD")
    tree = _git(cwd, "rev-parse", "HEAD^{tree}")
    parent = _git(cwd, "rev-parse", "HEAD^")
    if head == baseline or parent != baseline:
        raise HardFailure("Codex implementation candidate is not a source-bound successor")
    recovery = _preserve_late_source(cwd, todo_id=todo_id, candidate=head)
    if _source_status(cwd):
        raise HardFailure("Codex implementation candidate did not close cleanly")
    return head, tree, recovery


def _recover_existing_candidate(
    job: dict[str, Any], cwd: Path, *, todo_id: int
) -> dict[str, Any] | None:
    """Converge a dirty but already-closed descendant without rerunning Codex."""
    binding = job.get("plan_binding")
    baseline = binding.get("source_commit") if isinstance(binding, dict) else None
    if not isinstance(baseline, str) or len(baseline) != 40:
        return None
    head = _git(cwd, "rev-parse", "HEAD")
    if head == baseline:
        return None
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", baseline, head],
        cwd=cwd, check=False, capture_output=True,
    )
    if ancestor.returncode:
        return None
    tree = _git(cwd, "rev-parse", "HEAD^{tree}")
    baseline_tree = _git(cwd, "rev-parse", f"{baseline}^{{tree}}")
    if tree == baseline_tree:
        return None
    recovery = _preserve_late_source(cwd, todo_id=todo_id, candidate=head)
    if _source_status(cwd):
        raise HardFailure("Codex implementation could not recover existing candidate")
    artifacts: list[dict[str, Any]] = [
        {
            "kind": "closed_candidate",
            "commit": head,
            "tree": tree,
            "detail": "existing closed descendant recovered without rerunning the model",
        }
    ]
    if recovery:
        artifacts.append(
            {
                "kind": "late_source_recovery", "stash": recovery,
                "detail": "late source preserved outside the active worktree",
            }
        )
    return {
        "outcome": "satisfied",
        "established_conditions": ["implemented"],
        "artifacts": artifacts,
    }


def _run_with_lease(job: dict[str, Any], cwd: Path, *, invoke: Invoke = subprocess.run) -> dict[str, Any]:
    task = _validated_task(job)
    before_head = _git(cwd, "rev-parse", "HEAD")
    if _source_status(cwd):
        recovered = _recover_existing_candidate(
            job, cwd, todo_id=task["todo_id"]
        )
        if recovered is not None:
            return recovered
        raise HardFailure("Codex implementation requires a source-clean worktree")
    # Keep ephemeral auth and result files inside the isolated request worktree
    # rather than the host-wide /tmp namespace.  The directory is removed before
    # the runner evaluates Git state or emits a workflow outcome.
    with tempfile.TemporaryDirectory(prefix=".tgw-codex-implement-", dir=cwd) as temporary:
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
        _write_isolated_config(codex_home)
        schema_path.write_text(json.dumps(_FINAL_SCHEMA, sort_keys=True), encoding="utf-8")
        command = [
            _codex_binary(), "--ask-for-approval", "never",
            "--sandbox", "workspace-write", "exec", "--ephemeral",
            "-C", str(cwd),
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
    if after_head != before_head:
        return {
            "outcome": "conflict", "established_conditions": [],
            "artifacts": [{"kind": "boundary_violation", "detail": "Codex changed Git HEAD"}],
        }
    changed = bool(_source_status(cwd))
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
    diff_stat = _git(cwd, "diff", "--stat")
    artifacts = [
        {"kind": "codex_summary", "detail": report["summary"]},
        {"kind": "tests_reported", "tests": report["tests"]},
        {"kind": "git_diff", "detail": diff_stat},
    ]
    if report["status"] != "implemented" or not changed:
        return {"outcome": "partial", "established_conditions": [], "artifacts": artifacts}
    candidate, tree, recovery = _close_candidate(
        cwd, todo_id=task["todo_id"], baseline=before_head
    )
    artifacts.append(
        {"kind": "closed_candidate", "commit": candidate, "tree": tree}
    )
    if recovery:
        artifacts.append(
            {
                "kind": "late_source_recovery", "stash": recovery,
                "detail": "lease-violating late source preserved outside the active worktree",
            }
        )
    return {
        "outcome": "satisfied",
        "established_conditions": ["implemented"],
        "artifacts": artifacts,
    }


def run(job: dict[str, Any], cwd: Path, *, invoke: Invoke = subprocess.run) -> dict[str, Any]:
    with _exclusive_worktree_lease(cwd):
        return _run_with_lease(job, cwd, invoke=invoke)


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
