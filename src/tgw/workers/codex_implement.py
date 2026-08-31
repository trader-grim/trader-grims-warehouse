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
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from tgw.development.partial_resume import (
    HISTORY,
    PRESERVATION,
    candidate_changed_paths,
    classify,
    retire_preservation,
    source_tree,
)
from tgw.development.worktree_lease import exclusive_worktree_lease as _exclusive_worktree_lease
from tgw.development.worktree_lease import inherited_worktree_lease
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
            "type": "array",
            "maxItems": 50,
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
    "tgw_context_todo_exact",
    "tgw_context_todo_current",
    "tgw_context_todo_dependencies",
    "tgw_context_todo_inventory",
)

_MANUAL_REL = PurePosixPath(".tgw-coding-history/implementation/manual")
_MANUAL_TASK_NAME = "task.json"
_MANUAL_DONE_NAME = "done.json"
_MANUAL_EXECUTORS = frozenset({"codex", "manual", "claude"})


def _executor() -> str:
    value = os.environ.get("TGW_IMPLEMENT_EXECUTOR", "codex")
    if value not in _MANUAL_EXECUTORS:
        raise HardFailure(f"unsupported implementation executor: {value!r}")
    return value


def _summary_kind() -> str:
    ex = _executor()
    if ex == "manual":
        return "manual_summary"
    if ex == "claude":
        return "claude_summary"
    return "codex_summary"


def _failure_kind() -> str:
    ex = _executor()
    if ex == "manual":
        return "manual_failure"
    if ex == "claude":
        return "claude_failure"
    return "codex_failure"


def _manual_poll_seconds() -> float:
    try:
        return float(os.environ.get("TGW_IMPLEMENT_MANUAL_POLL", "5"))
    except ValueError as exc:
        raise HardFailure("manual implementation poll interval is invalid") from exc


def _manual_timeout_seconds() -> float:
    try:
        return float(os.environ.get("TGW_IMPLEMENT_MANUAL_TIMEOUT", "1500"))
    except ValueError as exc:
        raise HardFailure("manual implementation timeout is invalid") from exc


def _manual_task_payload(job: dict[str, Any], task: dict[str, Any], cwd: Path) -> dict[str, Any]:
    binding = job.get("plan_binding")
    return {
        "schema": "tgw-manual-implementation-task/v1",
        "todo_id": task["todo_id"],
        "body": task["body"],
        "worktree": str(cwd.resolve()),
        "plan_commit": binding.get("plan_commit") if isinstance(binding, Mapping) else None,
        "solution_hash": binding.get("solution_hash") if isinstance(binding, Mapping) else None,
        "source_commit": binding.get("source_commit") if isinstance(binding, Mapping) else None,
        **(
            {"plan_leaf": task["plan_leaf"]}
            if isinstance(task.get("plan_leaf"), Mapping)
            else {}
        ),
        "done_marker": str((cwd / _MANUAL_REL / _MANUAL_DONE_NAME).resolve()),
        "report_schema": _FINAL_SCHEMA,
        "boundaries": (
            "Work only in this request-bound worktree. Do not commit, deploy, change "
            "configuration or secrets, contact production, or create workflow receipt files. "
            "Implement the bounded task and run proportionate offline tests. When finished, "
            "write the done marker with the exact report shape; the wrapper validates source "
            "change, closes the exact candidate, and never accepts the marker as completion evidence."
        ),
    }


def _execute_manual(
    job: dict[str, Any], task: dict[str, Any], cwd: Path, before_head: str
) -> tuple[dict[str, Any] | None, Any]:
    """Supervised implementation handshake: card out, completion marker in.

    The operator/agent implements only inside the request-bound worktree and
    writes ``done.json`` with the ``_FINAL_SCHEMA`` report.  The wrapper still
    decides whether source changed, closes the exact successor commit, and
    never treats the marker as completion evidence.
    """
    root = cwd / _MANUAL_REL
    root.mkdir(parents=True, exist_ok=True)
    task_path = root / _MANUAL_TASK_NAME
    done_path = root / _MANUAL_DONE_NAME
    task_path.write_text(
        json.dumps(_manual_task_payload(job, task, cwd), sort_keys=True),
        encoding="utf-8",
    )
    # Any-actor model: the manual card must be group-readable so ANY
    # tgw-coders harness can read the task and write done.json.
    task_path.chmod(0o640)
    deadline = time.monotonic() + _manual_timeout_seconds()
    while time.monotonic() < deadline:
        if done_path.is_file():
            try:
                report = json.loads(done_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return (
                    {
                        "outcome": "failed",
                        "established_conditions": [],
                        "artifacts": [
                            {"kind": "manual_failure", "detail": f"invalid manual completion report: {exc}"}
                        ],
                    },
                    None,
                )
            return None, report
        time.sleep(_manual_poll_seconds())
    artifacts: list[dict[str, Any]] = [
        {
            "kind": "manual_timeout",
            "detail": "manual implementation timed out; task card retained",
        }
    ]
    if _source_status(cwd):
        recovery = _preserve_late_source(cwd, todo_id=task["todo_id"], candidate=before_head)
        artifacts.append(
            {
                "kind": "late_source_recovery",
                "stash": recovery,
                "detail": "manual late source preserved outside the active worktree for resume",
            }
        )
    return (
        {"outcome": "failed", "established_conditions": [], "artifacts": artifacts},
        None,
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
        ["git", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
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
    _validate_plan_leaf(task)
    return task


def _validate_plan_leaf(task: Mapping[str, Any]) -> None:
    """Require the exact approved-Plan leaf citation on every coding task.

    The Luet Plan-to-Todo bridge is the only producer of executable coding
    tasks; every such task must carry the Plan leaf it is bound to so the
    actor reads the approved leaf instead of replanning from the request.
    """
    leaf = task.get("plan_leaf")
    if not isinstance(leaf, Mapping) or leaf.get("schema") != "tgw-plan-leaf-citation/v1":
        raise HardFailure("Codex implementation task lacks an exact Plan leaf citation")
    for field in (
        "plan_commit",
        "solution_hash",
        "closure_hash",
        "capability",
        "treatment_id",
        "source_commit",
    ):
        value = leaf.get(field)
        if not isinstance(value, str) or not value:
            raise HardFailure(f"Codex implementation Plan leaf citation lacks {field}")


def _codex_binary() -> str:
    configured = os.environ.get("TGW_CODEX_BIN")
    candidate = Path(configured) if configured else Path.home() / ".local/bin/codex"
    if not candidate.is_absolute() or not candidate.is_file() or not os.access(candidate, os.X_OK):
        fallback = shutil.which("codex")
        if not fallback:
            raise HardFailure("dedicated Codex executable is unavailable")
        candidate = Path(fallback)
    return str(candidate.resolve())


def _codex_auth_path() -> Path:
    """Return the dedicated implementation actor's native Codex credential."""
    return Path.home() / ".codex" / "auth.json"


def _claude_binary() -> str:
    configured = os.environ.get("TGW_CLAUDE_BIN")
    candidate = Path(configured) if configured else shutil.which("claude")
    if not candidate or not os.access(candidate, os.X_OK):
        raise HardFailure(
            "Claude Code executable is unavailable (install and authenticate it "
            "for the worker identity, then set TGW_CLAUDE_BIN)"
        )
    return str(candidate)


def _claude_report(stdout: str) -> dict[str, Any] | None:
    """Extract the final report JSON object from Claude Code's print-mode output.

    Claude -p --output-format json emits JSONL; the prompt instructs the model
    to END its final result text with exactly one JSON report object matching
    _FINAL_SCHEMA, so the last JSON object in the output is the report.  The
    extraction tolerates markdown fences, trailing prose, and JSONL payloads
    that carry the report object directly (instead of inside a text string).
    """
    text = None
    direct: dict[str, Any] | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            text = line
            continue
        if not isinstance(value, dict):
            continue
        payload = value.get("result") or value.get("text") or value.get("content")
        if isinstance(payload, str) and payload:
            text = payload
        elif isinstance(payload, dict):
            # Some Claude JSONL variants place the final object in a payload
            # field directly; keep the last one seen as a fallback.
            direct = payload
    if isinstance(text, str) and text:
        return _last_json_object(text)
    return direct


def _last_json_object(text: str) -> dict[str, Any] | None:
    """Return the last complete JSON object embedded in free text.

    Claude's print-mode ``result`` text is a plain string; the model may wrap
    the report in a markdown code fence or append closing prose.  Walk the
    last ``{`` backward so any trailing fence/prose after the object is
    tolerated, and accept only a dict-shaped parse.
    """
    decoder = json.JSONDecoder()
    search = text
    while True:
        brace = search.rfind("{")
        if brace < 0:
            return None
        candidate = search[brace:]
        try:
            value, _end = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            search = search[:brace]
            continue
        if isinstance(value, dict):
            return value
        search = search[:brace]


def _plan_leaf_brief(task: Mapping[str, Any]) -> str:
    """Render the exact approved-Plan leaf citation for the actor.

    The actor must read the bound Plan leaf and implement only it; it must
    never replan, re-derive, or broaden the task from the request text.  A
    request that does not map onto the bound leaf is reported blocked, never
    silently redirected into a new plan.
    """
    leaf = task.get("plan_leaf")
    if not isinstance(leaf, Mapping):
        return ""
    return f"""BOUND PLAN LEAF (read this exact leaf before implementing; do not replan):
  plan_commit: {leaf.get("plan_commit")}
  capability: {leaf.get("capability")}
  treatment_id: {leaf.get("treatment_id")}
  source_commit: {leaf.get("source_commit")}
  solution_hash: {leaf.get("solution_hash")}
  closure_hash: {leaf.get("closure_hash")}

This task is the bounded execution of the approved Plan leaf cited above.
Implement exactly that leaf. If the request or body appears to require a
different capability, treatment, or plan change, do NOT replan or improvise a
new scope: return status=blocked and name the exact gap in the summary.
"""


def _prompt(task: dict[str, Any], continuation: dict[str, Any] | None = None, *, treatment: str = "Codex") -> str:
    continuation_brief = ""
    if continuation:
        continuation_brief = f"""
This is an exact bounded continuation of a preserved partial attempt.
Preserve every current source byte. Continue from attempt {continuation["resume_of"]}
with source fingerprint {continuation["resume_fingerprint"]}. Do not restart,
clean, reset, stash, or replace the existing implementation.
"""
    remediation_brief = ""
    remediation = task.get("remediation")
    if isinstance(remediation, dict):
        findings = remediation.get("diagnostic_findings")
        findings_brief = (
            json.dumps(findings, sort_keys=True, indent=2)
            if isinstance(findings, list) and findings
            else "[]"
        )
        remediation_brief = f"""
This is remediation generation {remediation.get("generation")} for the exact
closed candidate {remediation.get("candidate_commit")}. Address the diagnostic
findings below by producing one clean successor. Do not restart the original
implementation or change unrelated behavior.

Failed stage: {remediation.get("failed_stage")}
Summary: {remediation.get("reason")}
Validated findings:
{findings_brief}
Evidence: {remediation.get("failure_receipt_hash")}
"""
    return f"""You are the {treatment} implementation treatment for TGW Todo #{task["todo_id"]}.

Repository AGENTS.md is your actor contract. CLAUDE.md does not govern Codex.
Work only in the current request-bound worktree. Do not commit, deploy, change
configuration or secrets, contact production, access satellite machines, import
memory, or create workflow receipt files. Implement only this bounded task and
run proportionate offline tests:
{continuation_brief}
{remediation_brief}

{_plan_leaf_brief(task)}

{task["body"]}

Return the requested JSON report. Use status=blocked if the task cannot be
implemented inside these boundaries. The wrapper independently determines
whether source changed, closes an exact commit after a successful report, and
does not accept your report as completion evidence.
""" + (
        f"""
The final report object MUST be the very last thing you output, with no
markdown fence, prose, or punctuation before or after it.  It MUST match this
exact JSON schema:

{json.dumps(_FINAL_SCHEMA, sort_keys=True)}
"""
        if treatment == "Claude"
        else ""
    )


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

_TRANSIENT_CACHE_ROOTS = frozenset({".pytest_cache", ".ruff_cache"})


def _source_pathspec() -> tuple[str, ...]:
    return (
        ".",
        *(f":(exclude){name}" for name in sorted(_RECEIPT_FILES)),
        f":(exclude){HISTORY}",
        f":(exclude){PRESERVATION}",
    )


def _ignored_paths(cwd: Path) -> tuple[str, ...]:
    """Return ignored untracked paths without lossy newline parsing."""
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--others",
            "--ignored",
            "--exclude-standard",
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace")[-300:]
        raise HardFailure(f"Codex implementation ignored-file probe failed: {detail}")
    return tuple(item.decode("utf-8", errors="surrogateescape") for item in completed.stdout.split(b"\0") if item)


def _transient_cache_roots(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Select exact generated-cache roots while retaining every other ignore."""
    roots: set[PurePosixPath] = set()
    for value in paths:
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise HardFailure("Codex implementation received an unsafe ignored path")
        if relative.parts[0] in _TRANSIENT_CACHE_ROOTS:
            roots.add(PurePosixPath(relative.parts[0]))
            continue
        try:
            cache_index = relative.parts.index("__pycache__")
        except ValueError:
            continue
        roots.add(PurePosixPath(*relative.parts[: cache_index + 1]))
    return tuple(str(root) for root in sorted(roots, key=str))


def _purge_transient_caches(cwd: Path) -> tuple[str, ...]:
    """Remove only known generated caches from the leased coding worktree."""
    roots = _transient_cache_roots(_ignored_paths(cwd))
    for root in roots:
        completed = subprocess.run(
            ["git", "clean", "-fdX", "--", f":(literal){root}"],
            cwd=cwd,
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode:
            raise HardFailure(f"Codex implementation could not remove generated worktree cache: {completed.stderr[-300:]}")
    remaining = _transient_cache_roots(_ignored_paths(cwd))
    if remaining:
        raise HardFailure("Codex implementation could not clean generated worktree caches")
    return roots


def _source_status(cwd: Path) -> tuple[str, ...]:
    """Return mutable source entries while excluding workflow evidence files."""
    status = _git(
        cwd,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignored=matching",
    )
    return tuple(line for line in status.splitlines() if line[3:] not in _RECEIPT_FILES and not line[3:].startswith(HISTORY + "/") and not line[3:].startswith(PRESERVATION + "/"))


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
        raise HardFailure(f"Codex implementation could not recover its Git index: {completed.stderr[-300:]}")


def _preserve_late_source(cwd: Path, *, todo_id: int, candidate: str) -> str | None:
    """Move a lease-violating late write to Git's recovery stash, losslessly."""
    if not _source_status(cwd):
        return None
    before = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/stash"],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    ).stdout.strip()
    pathspec = _source_pathspec()
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


def _close_candidate(cwd: Path, *, todo_id: int, baseline: str) -> tuple[str, str, str | None]:
    """Commit only the implementation bytes and return the exact commit/tree."""
    pathspec = _source_pathspec()
    try:
        _git(cwd, "add", "-A", "--", *pathspec)
        staged = _git(cwd, "diff", "--cached", "--name-only", "--", *pathspec)
        if not staged:
            raise HardFailure("Codex implementation produced no source bytes to close")
        # The lease fences cooperating TGW actors. These final checks also keep
        # a non-cooperating writer's later bytes out of the staged candidate.
        unstaged = _git(cwd, "diff", "--name-only", "--", *pathspec)
        untracked = tuple(
            item
            for item in _git(cwd, "ls-files", "--others", "--exclude-standard").splitlines()
            if item not in _RECEIPT_FILES and not item.startswith(HISTORY + "/") and not item.startswith(PRESERVATION + "/")
        )
        ignored = "\n".join(
            item for item in _git(cwd, "ls-files", "--others", "--ignored", "--exclude-standard").splitlines() if not item.startswith(HISTORY + "/") and not item.startswith(PRESERVATION + "/")
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


def _recover_existing_candidate(job: dict[str, Any], cwd: Path, *, todo_id: int) -> dict[str, Any] | None:
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
        cwd=cwd,
        check=False,
        capture_output=True,
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
    retirement = retire_preservation(cwd, todo_id=todo_id, candidate_commit=head)
    artifacts: list[dict[str, Any]] = [
        {
            "kind": "closed_candidate",
            "commit": head,
            "tree": tree,
            "base_commit": baseline,
            "changed_paths": candidate_changed_paths(cwd, baseline, head),
            "detail": "existing closed descendant recovered without rerunning the model",
        }
    ]
    if recovery:
        artifacts.append(
            {
                "kind": "late_source_recovery",
                "stash": recovery,
                "detail": "late source preserved outside the active worktree",
            }
        )
    if retirement:
        artifacts.append({"kind": "preservation_retirement", "archive": retirement["archive"], "receipt_sha256": retirement["receipt_sha256"]})
    return {
        "outcome": "satisfied",
        "established_conditions": ["implemented"],
        "artifacts": artifacts,
    }


def _run_with_lease(job: dict[str, Any], cwd: Path, *, invoke: Invoke = subprocess.run) -> dict[str, Any]:
    task = _validated_task(job)
    before_head = _git(cwd, "rev-parse", "HEAD")
    cleaned_caches = set(_purge_transient_caches(cwd))
    continuation = None
    if _source_status(cwd):
        binding = job.get("plan_binding")
        expected = {
            "job_id": None,
            "attempt_count": None,
            "todo_id": job.get("todo_id"),
            "plan_commit": binding.get("plan_commit") if isinstance(binding, dict) else None,
            "solution_hash": binding.get("solution_hash") if isinstance(binding, dict) else None,
            "source_commit": binding.get("source_commit") if isinstance(binding, dict) else None,
            "source_tree": (source_tree(cwd, binding["source_commit"]) if isinstance(binding, dict) and isinstance(binding.get("source_commit"), str) else None),
            "actor": job.get("todo_agent"),
            "worktree": str(cwd.resolve()),
            "treatment_id": "codex-implement",
            "treatment_version": str(job.get("treatment_version", "1")),
        }
        state = classify(cwd, expected)
        if not state["resumable"] or job.get("resume_of") != state.get("resume_of") or job.get("resume_fingerprint") != state.get("fingerprint"):
            recovered = _recover_existing_candidate(job, cwd, todo_id=task["todo_id"])
            if recovered is not None:
                return recovered
            raise HardFailure("Codex implementation requires a source-clean worktree unless the dirty resume binding and fingerprint are exact")
        continuation = {"resume_of": state["resume_of"], "resume_fingerprint": state["fingerprint"]}
    # Keep ephemeral auth and result files inside the isolated request worktree
    # rather than the host-wide /tmp namespace.  The directory is removed before
    # the runner evaluates Git state or emits a workflow outcome.
    early_result: dict[str, Any] | None = None
    report: Any = None
    try:
        if _executor() == "manual":
            early_result, report = _execute_manual(job, task, cwd, before_head)
        elif _executor() == "claude":
            with tempfile.TemporaryDirectory(prefix=".tgw-claude-implement-", dir=cwd) as temporary:
                temp = Path(temporary)
                schema_path = temp / "schema.json"
                schema_path.write_text(json.dumps(_FINAL_SCHEMA, sort_keys=True), encoding="utf-8")
                command = [
                    _claude_binary(),
                    "-p",
                    "--output-format",
                    "json",
                    "--permission-mode",
                    "bypassPermissions",
                ]
                completed = invoke(
                    command,
                    cwd=cwd,
                    input=_prompt(task, continuation, treatment="Claude"),
                    text=True,
                    capture_output=True,
                    check=False,
                    env={**os.environ},
                )
                if completed.returncode:
                    early_result = {
                        "outcome": "failed",
                        "established_conditions": [],
                        "artifacts": [{"kind": _failure_kind(), "detail": completed.stderr[-1000:] or completed.stdout[-1000:]}],
                    }
                else:
                    report = _claude_report(completed.stdout)
                    if report is None:
                        early_result = {
                            "outcome": "failed",
                            "established_conditions": [],
                            "artifacts": [{"kind": _failure_kind(), "detail": "claude final report is not parseable"}],
                        }
        else:
            with tempfile.TemporaryDirectory(prefix=".tgw-codex-implement-", dir=cwd) as temporary:
                temp = Path(temporary)
                schema_path, output_path = temp / "schema.json", temp / "result.json"
                codex_home = temp / "codex-home"
                codex_home.mkdir(mode=0o700)
                source_auth = _codex_auth_path()
                if not source_auth.is_file():
                    raise HardFailure("dedicated Codex authentication is unavailable")
                destination_auth = codex_home / "auth.json"
                shutil.copyfile(source_auth, destination_auth)
                destination_auth.chmod(0o600)
                _write_isolated_config(codex_home)
                schema_path.write_text(json.dumps(_FINAL_SCHEMA, sort_keys=True), encoding="utf-8")
                command = [
                    _codex_binary(),
                    "--ask-for-approval",
                    "never",
                    "--sandbox",
                    "danger-full-access",
                    "exec",
                    "--ephemeral",
                    "-C",
                    str(cwd),
                    "--output-schema",
                    str(schema_path),
                    "-o",
                    str(output_path),
                    "-",
                ]
                completed = invoke(
                    command,
                    cwd=cwd,
                    input=_prompt(task, continuation),
                    text=True,
                    capture_output=True,
                    check=False,
                    env={**os.environ, "CODEX_HOME": str(codex_home)},
                )
                if completed.returncode:
                    early_result = {
                        "outcome": "failed",
                        "established_conditions": [],
                        "artifacts": [{"kind": "codex_failure", "detail": completed.stderr[-1000:]}],
                    }
                else:
                    try:
                        report = json.loads(output_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as exc:
                        early_result = {
                            "outcome": "failed",
                            "established_conditions": [],
                            "artifacts": [
                                {
                                    "kind": "codex_failure",
                                    "detail": f"invalid final report: {exc}",
                                }
                            ],
                        }
    finally:
        cleaned_caches.update(_purge_transient_caches(cwd))
        manual_root = cwd / _MANUAL_REL
        if manual_root.is_dir():
            shutil.rmtree(manual_root, ignore_errors=True)

    cleanup_artifact = (
        {
            "kind": "transient_cache_cleanup",
            "paths": sorted(cleaned_caches),
            "detail": "removed generated caches without touching ignored work",
        }
        if cleaned_caches
        else None
    )
    if early_result is not None:
        if cleanup_artifact is not None:
            early_result["artifacts"].append(cleanup_artifact)
        return early_result
    after_head = _git(cwd, "rev-parse", "HEAD")
    if after_head != before_head:
        return {
            "outcome": "conflict",
            "established_conditions": [],
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
            "outcome": "failed",
            "established_conditions": [],
            "artifacts": [{"kind": _failure_kind(), "detail": "final report violates runner contract"}],
        }
    artifacts = [
        {"kind": _summary_kind(), "detail": report["summary"]},
        {"kind": "tests_reported", "tests": report["tests"]},
    ]
    if cleanup_artifact is not None:
        artifacts.append(cleanup_artifact)
    if report["status"] != "implemented" or not changed:
        return {"outcome": "partial", "established_conditions": [], "artifacts": artifacts}
    candidate, tree, recovery = _close_candidate(cwd, todo_id=task["todo_id"], baseline=before_head)
    retirement = retire_preservation(cwd, todo_id=task["todo_id"], candidate_commit=candidate)
    changed_paths = candidate_changed_paths(cwd, before_head, candidate)
    artifacts.append({"kind": "git_diff", "detail": _git(cwd, "diff", "--stat", "--no-renames", f"{before_head}..{candidate}"), "changed_paths": changed_paths})
    artifacts.append(
        {
            "kind": "closed_candidate",
            "commit": candidate,
            "tree": tree,
            "base_commit": before_head,
            "changed_paths": changed_paths,
        }
    )
    if recovery:
        artifacts.append(
            {
                "kind": "late_source_recovery",
                "stash": recovery,
                "detail": "lease-violating late source preserved outside the active worktree",
            }
        )
    if retirement:
        artifacts.append({"kind": "preservation_retirement", "archive": retirement["archive"], "receipt_sha256": retirement["receipt_sha256"]})
    return {
        "outcome": "satisfied",
        "established_conditions": ["implemented"],
        "artifacts": artifacts,
    }


def run(job: dict[str, Any], cwd: Path, *, invoke: Invoke = subprocess.run) -> dict[str, Any]:
    inherited = os.environ.get("TGW_CODING_WORKTREE_LEASE_FD")
    if inherited is not None:
        try:
            descriptor = int(inherited)
        except ValueError as exc:
            raise HardFailure("coding runner inherited a malformed lease descriptor") from exc
        lease = inherited_worktree_lease(cwd, descriptor)
    else:
        lease = _exclusive_worktree_lease(cwd)
    with lease:
        return _run_with_lease(job, cwd, invoke=invoke)


def main() -> int:
    try:
        result = run(_job_from_environment(), Path.cwd())
    except Exception as exc:  # runner protocol must remain structured
        result = {
            "outcome": "failed",
            "established_conditions": [],
            "artifacts": [{"kind": "runner_failure", "detail": str(exc)}],
        }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
