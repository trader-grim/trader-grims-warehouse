"""Apparatus-free implement / review session runners for the continual
harness — Todo 1916 leaf 11.1.

The old ``workers/codex_implement.py`` and ``development/coding_review.py``
runners are bound to the deleted apparatus: they demand a Luet plan-leaf
citation, close their own candidate commits, write workflow receipts, and
classify partial-resume state. The continual-harness orchestrator does none of
that — it squashes and fast-forwards itself (``harness_git``) and keeps the
process in the ledger.

So these runners are deliberately thin. Given a task body and a worktree they
spawn one fresh coding session (codex or claude), let it change source in the
worktree, and write a small receipt the orchestrator's ``harness_runners``
mappers understand. They commit nothing, gate nothing, and are not trusted for
correctness.

Job (``TGW_CODING_JOB``): ``{"task_id", "body", "worktree", "round"?,
"prior_findings"?}``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

Invoke = Callable[..., "subprocess.CompletedProcess[str]"]

_CONTEXT_MCP = Path("/opt/TGW/tgw-lib/bin/tgw-context-mcp")
_CONTEXT_TOOLS = (
    "tgw_context_code_graph", "tgw_context_bundle", "tgw_context_plan_graph",
    "tgw_context_plan_source", "tgw_context_status", "tgw_context_onboarding",
    "tgw_context_runbooks", "tgw_context_todo_exact",
)

_IMPLEMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary"],
    "properties": {
        "status": {"enum": ["implemented", "blocked"]},
        "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
        "tests": {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 1000}},
    },
}

_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "findings"],
    "properties": {
        "verdict": {"enum": ["pass", "findings"]},
        "findings": {
            "type": "array", "maxItems": 50,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["message", "blocking"],
                "properties": {
                    "message": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "blocking": {"type": "boolean"},
                },
            },
        },
    },
}

_BOUNDARIES = (
    "Work only in this request-bound worktree. Do NOT commit, push, deploy, "
    "change configuration or secrets, contact production, or write any receipt "
    "file. The harness squashes your work to one commit and runs the test suite "
    "itself; your report is not accepted as completion evidence."
)


class SessionError(RuntimeError):
    """The coding session could not be run by any executor in the chain."""


class SessionUnavailable(RuntimeError):
    """One executor could not be used (binary absent, no credential, auth/quota
    wall). The chain moves to the next executor; it is not a task failure."""


# --------------------------------------------------------------------------- #
# executor selection — an ordered chain, not one choice
# --------------------------------------------------------------------------- #

_EXECUTORS = ("claude", "codex", "stub")
_DEFAULT_CHAIN = ("claude", "codex")
# 'stub' is the offline bootstrap executor (LEAF-11-9 W0). It is never in the
# default chain or a model_selector result — it runs only when a caller asks for
# it by name (executor_preference / TGW_HARNESS_EXECUTOR).


def _executor_chain(job: dict[str, Any] | None = None) -> list[str]:
    """The ordered list of executors to try. The first that has a credential and
    produces a parseable report wins; each that is unavailable or fails is
    skipped (leaf 11.8: 'a lapsed provider is a dispatch-time reroute, never a
    dead-letter'). Preference sources, highest first:

      job["executor_preference"] (list) / job["executor"] (single)
      TGW_HARNESS_EXECUTOR
      tgw.model_selector ranking (put its pick first, rest of the default after)
      _DEFAULT_CHAIN
    """
    if job:
        pref = job.get("executor_preference")
        if isinstance(pref, str):
            pref = [pref]
        if not pref and job.get("executor"):
            pref = [job["executor"]]
        chain = [e for e in (pref or []) if e in _EXECUTORS]
        if chain:
            return chain
    forced = os.environ.get("TGW_HARNESS_EXECUTOR")
    if forced in _EXECUTORS:
        return [forced]
    try:
        from tgw.model_selector import select_executor

        selection = select_executor("implementation")
        if selection.status == "SELECTED" and selection.executor in _EXECUTORS:
            first = selection.executor
            return [first, *[e for e in _DEFAULT_CHAIN if e != first]]
    except Exception:
        pass  # advisory; fall through to the default chain
    return list(_DEFAULT_CHAIN)


def _session_credential(executor: str) -> tuple[str, str] | None:
    """(env_var, value) for *executor*'s model credential, from the secrets
    facility. None means no env credential — the executor may still be usable
    from a file login in the coder's home (codex ~/.codex/auth.json), which the
    per-executor runner checks."""
    try:
        from tgw.config import DEFAULT_CONFIG, load_config

        load_config(DEFAULT_CONFIG)  # sources secrets_root/tgw.env; harmless if absent
    except Exception:
        pass
    names = {
        "claude": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
        "codex": ("CODEX_API_KEY", "OPENAI_API_KEY"),
    }.get(executor, (f"{executor.upper()}_API_KEY",))
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return name, value
    return None


def _codex_binary(*, optional: bool = False) -> str | None:
    configured = os.environ.get("TGW_CODEX_BIN")
    candidate = Path(configured) if configured else Path.home() / ".local/bin/codex"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())
    found = shutil.which("codex")
    if found:
        return found
    if optional:
        return None
    raise SessionError("codex executable is unavailable")


def _claude_binary() -> str:
    configured = os.environ.get("TGW_CLAUDE_BIN")
    candidate = configured or shutil.which("claude")
    if not candidate or not os.access(candidate, os.X_OK):
        raise SessionError("claude executable is unavailable")
    return candidate


# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #

def implement_prompt(job: dict[str, Any]) -> str:
    prior = job.get("prior_findings") or []
    prior_brief = ""
    if prior:
        prior_brief = (
            "\nThis is a remediation round. A previous attempt left these "
            "unresolved points; address them without changing unrelated "
            "behaviour:\n"
            + json.dumps(prior, indent=2, sort_keys=True)
            + "\n"
        )
    return f"""You are the implementation session for TGW task {job["task_id"]} (round {job.get("round", 1)}).

Repository AGENTS.md is your actor contract. {_BOUNDARIES}
{prior_brief}
TASK:
{job["body"]}

Implement the bounded task and run proportionate offline tests. Then output,
as the very last thing with no fence or prose around it, one JSON object
matching exactly:

{json.dumps(_IMPLEMENT_SCHEMA, sort_keys=True)}

Use status "blocked" (with the reason in summary) if it cannot be done inside
these boundaries.
"""


def review_prompt(job: dict[str, Any]) -> str:
    return f"""You are an independent diagnostic reviewer for TGW task {job["task_id"]}.

Another session implemented the task below in this worktree. Review the working
tree changes (`git diff` against the branch point). {_BOUNDARIES}

You are non-admitting: your findings inform whether the harness runs another
remediation round; they do not by themselves block the change. Mark a finding
`blocking: true` only for a correctness defect or a violated task requirement,
not for style.

TASK:
{job["body"]}

Output, as the very last thing with no fence or prose around it, one JSON
object matching exactly:

{json.dumps(_REVIEW_SCHEMA, sort_keys=True)}
"""


# --------------------------------------------------------------------------- #
# claude report extraction (stable utility, mirrors codex_implement)
# --------------------------------------------------------------------------- #

def _last_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    last: dict[str, Any] | None = None
    index = 0
    while True:
        brace = text.find("{", index)
        if brace < 0:
            return last
        try:
            value, end = decoder.raw_decode(text, brace)
        except json.JSONDecodeError:
            index = brace + 1
            continue
        if isinstance(value, dict):
            last = value
        index = end


def _claude_report(stdout: str) -> dict[str, Any] | None:
    text: str | None = None
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
            direct = payload
    if isinstance(text, str) and text:
        return _last_json_object(text)
    return direct


# --------------------------------------------------------------------------- #
# session execution
# --------------------------------------------------------------------------- #

def _write_isolated_codex_config(codex_home: Path) -> None:
    if not (_CONTEXT_MCP.is_file() and os.access(_CONTEXT_MCP, os.X_OK)):
        return
    lines = [
        "[mcp_servers.tgw-context]\n",
        f"command = {json.dumps(str(_CONTEXT_MCP))}\n",
        "args = []\n",
    ]
    for tool in _CONTEXT_TOOLS:
        lines += [f"\n[mcp_servers.tgw-context.tools.{tool}]\n", 'approval_mode = "approve"\n']
    (codex_home / "config.toml").write_text("".join(lines), encoding="utf-8")
    (codex_home / "config.toml").chmod(0o600)


_UNAVAILABLE_PATTERNS = (
    "usage limit", "quota", "rate limit", "429", "not authenticated",
    "unauthorized", "401", "invalid api key", "login", "credit balance",
    "subscription", "try again at",
)


def _looks_unavailable(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in _UNAVAILABLE_PATTERNS)


def _secret_scrubbed_env() -> dict[str, str]:
    """The parent environment with every provider secret removed.

    ``_session_credential`` sources the whole ``tgw.env`` into this process, so
    without this a bypassPermissions session would inherit every coding
    provider's key. The session gets back exactly one credential — the
    selected executor's — and nothing else (the cross-contamination boundary
    Dave flagged; the leaf 11.8 broker enforces it properly later)."""
    def is_secret(name: str) -> bool:
        u = name.upper()
        return (
            u.endswith("_API_KEY") or u.endswith("_TOKEN") or "OAUTH" in u
            or u.startswith(("ANTHROPIC", "CLAUDE_", "OPENAI", "CODEX", "GOOGLE_API"))
        )
    return {k: v for k, v in os.environ.items() if not is_secret(k)}


def _run_claude(prompt: str, worktree: Path, *, invoke: Invoke) -> dict[str, Any] | None:
    # Independence (W07): implement / review must not share Claude Code's
    # per-project state (~/.claude/projects/* — transcript, session cache). A
    # fresh HOME per session guarantees it; the credential comes from the
    # secrets facility, never the caller's live login dir.
    if not shutil.which(os.environ.get("TGW_CLAUDE_BIN") or "claude"):
        raise SessionUnavailable("claude executable not on PATH")
    cred = _session_credential("claude")
    with tempfile.TemporaryDirectory(prefix=".tgw-harness-claude-", dir=worktree) as tmp:
        home = Path(tmp) / "home"
        (home / ".claude").mkdir(parents=True, mode=0o700)
        env = _secret_scrubbed_env()
        env["HOME"] = str(home)
        env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
        if cred:
            env[cred[0]] = cred[1]
        completed = invoke(
            [_claude_binary(), "-p", "--output-format", "json",
             "--permission-mode", "bypassPermissions"],
            cwd=worktree, input=prompt, text=True, capture_output=True, check=False,
            env=env, timeout=_timeout_s(),
        )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-600:]
        if not cred or _looks_unavailable(detail):
            raise SessionUnavailable(f"claude unavailable: {detail or 'no credential'}")
        raise SessionError(f"claude session exit {completed.returncode}: {detail}")
    return _claude_report(completed.stdout)


def _run_codex(prompt: str, worktree: Path, schema: dict[str, Any], *, invoke: Invoke) -> dict[str, Any] | None:
    if not _codex_binary(optional=True):
        raise SessionUnavailable("codex executable not on PATH")
    cred = _session_credential("codex")
    file_auth = (Path.home() / ".codex" / "auth.json").is_file()
    if not cred and not file_auth:
        raise SessionUnavailable("codex: no CODEX/OPENAI key and no ~/.codex/auth.json")
    with tempfile.TemporaryDirectory(prefix=".tgw-harness-session-", dir=worktree) as tmp:
        temp = Path(tmp)
        schema_path, output_path = temp / "schema.json", temp / "result.json"
        codex_home = temp / "codex-home"
        codex_home.mkdir(mode=0o700)
        if file_auth:
            shutil.copyfile(Path.home() / ".codex" / "auth.json", codex_home / "auth.json")
            (codex_home / "auth.json").chmod(0o600)
        _write_isolated_codex_config(codex_home)
        schema_path.write_text(json.dumps(schema, sort_keys=True), encoding="utf-8")
        env = _secret_scrubbed_env()
        env["CODEX_HOME"] = str(codex_home)
        env["HOME"] = str(temp / "home")
        Path(env["HOME"]).mkdir(exist_ok=True)
        if cred:
            env[cred[0]] = cred[1]
        completed = invoke(
            [_codex_binary(), "--ask-for-approval", "never", "--sandbox",
             "danger-full-access", "exec", "--ephemeral", "-C", str(worktree),
             "--output-schema", str(schema_path), "-o", str(output_path), "-"],
            cwd=worktree, input=prompt, text=True, capture_output=True, check=False,
            env=env, timeout=_timeout_s(),
        )
        if completed.returncode:
            detail = completed.stderr.strip()[-600:]
            if _looks_unavailable(detail):
                raise SessionUnavailable(f"codex unavailable: {detail}")
            raise SessionError(f"codex session exit {completed.returncode}: {detail}")
        try:
            return json.loads(output_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return _last_json_object(completed.stdout)


_STUB_CANARY = ".tgw-canary"


def _run_stub(worktree: Path, schema: dict[str, Any]) -> dict[str, Any]:
    """The offline bootstrap executor — LEAF-11-9 W0.

    No binary, no network, no credential. It makes one deterministic change so
    the orchestrator can prove the whole
    worktree -> pytest_gate -> harness_git squash+FF -> main_ref_guard -> ledger
    path with no LLM (harness onboarding must not require a working model).
    Selected only when the chain explicitly names 'stub'.
    """
    if "verdict" in schema.get("properties", {}):
        return {"verdict": "pass", "findings": []}
    canary = Path(worktree) / _STUB_CANARY
    canary.write_text(f"harness offline canary — {Path(worktree).name}\n", encoding="utf-8")
    return {
        "status": "implemented",
        "summary": f"offline stub canary wrote {_STUB_CANARY}",
        "tests": [],
    }


def _timeout_s() -> int:
    try:
        return int(os.environ.get("TGW_HARNESS_SESSION_TIMEOUT", "1800"))
    except ValueError:
        return 1800


def _apply_executor_bins(job: dict[str, Any]) -> None:
    """Honour explicit executor binary paths carried on the job.

    The confined coder identity runs under ``sudo`` with a minimal
    ``secure_path`` and cannot discover ``claude`` when it lives under an
    operator home; the orchestrator resolves the path and passes it here."""
    bins = job.get("executor_bin")
    if not isinstance(bins, dict):
        return
    if bins.get("claude"):
        os.environ["TGW_CLAUDE_BIN"] = str(bins["claude"])
    if bins.get("codex"):
        os.environ["TGW_CODEX_BIN"] = str(bins["codex"])


def _dispatch_chain(
    job: dict[str, Any], prompt: str, schema: dict[str, Any], *, invoke: Invoke,
) -> tuple[str, dict[str, Any] | None, list[str]]:
    """Try each executor in the chain. Return (executor, report, skipped) for
    the first that runs and produces something; on total exhaustion return
    ("", None, skipped) with the per-executor reasons."""
    _apply_executor_bins(job)
    worktree = Path(job["worktree"])
    skipped: list[str] = []
    for executor in _executor_chain(job):
        try:
            if executor == "stub":
                report = _run_stub(worktree, schema)
            elif executor == "claude":
                report = _run_claude(prompt, worktree, invoke=invoke)
            else:
                report = _run_codex(prompt, worktree, schema, invoke=invoke)
        except SessionUnavailable as exc:
            skipped.append(f"{executor}: {exc}")
            continue
        return executor, report, skipped
    return "", None, skipped


def run_implement_session(job: dict[str, Any], *, invoke: Invoke = subprocess.run) -> dict[str, Any]:
    executor, report, skipped = _dispatch_chain(
        job, implement_prompt(job), _IMPLEMENT_SCHEMA, invoke=invoke,
    )
    if not executor:
        return {"outcome": "failed", "artifacts": [{"kind": "executor_chain_exhausted",
                "detail": "; ".join(skipped) or "no executor available"}]}
    if not isinstance(report, dict) or report.get("status") not in {"implemented", "blocked"}:
        return {"outcome": "failed", "artifacts": [{"kind": f"{executor}_failure",
                "detail": "session returned no parseable report"},
                *([{"kind": "executor_chain_skipped", "detail": "; ".join(skipped)}] if skipped else [])]}
    status = report["status"]
    return {
        "outcome": "satisfied" if status == "implemented" else "blocked",
        "artifacts": [{"kind": f"{executor}_summary", "detail": str(report.get("summary", ""))[:2000]}],
        "tests_reported": list(report.get("tests", [])),
    }


def run_review_session(job: dict[str, Any], *, invoke: Invoke = subprocess.run) -> dict[str, Any]:
    executor, report, _skipped = _dispatch_chain(
        job, review_prompt(job), _REVIEW_SCHEMA, invoke=invoke,
    )
    if not executor or not isinstance(report, dict) or "findings" not in report:
        # review is non-gating (the test suite is the gate); a reviewer that
        # produced nothing is one non-blocking finding, not a task failure.
        return {"verdict": "FINDINGS", "findings": [
            {"message": "review session returned no parseable report", "blocking": False}
        ]}
    findings = [
        {"message": str(f.get("message", ""))[:2000], "blocking": bool(f.get("blocking"))}
        for f in report.get("findings", [])
        if isinstance(f, dict) and str(f.get("message", "")).strip()
    ]
    verdict = "PASS" if (str(report.get("verdict")) == "pass" and not findings) else "FINDINGS"
    return {"verdict": verdict, "findings": findings}


# --------------------------------------------------------------------------- #
# console entrypoints — write the receipt the orchestrator's runner reads
# --------------------------------------------------------------------------- #

def _load_job() -> dict[str, Any]:
    """The job comes from ``TGW_CODING_JOB`` when the env survives, else from
    the nearest ``.tgw-harness/job.json`` at or above the cwd — the reliable
    path when the session is invoked via ``sudo -n -u tgw-coder`` (sudo strips
    the environment) with cwd set to ``<worktree>/src``."""
    raw = os.environ.get("TGW_CODING_JOB", "")
    if not raw:
        here = Path.cwd()
        for base in (here, *here.parents):
            job_file = base / ".tgw-harness" / "job.json"
            if job_file.is_file():
                raw = job_file.read_text(encoding="utf-8")
                break
    try:
        job = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SessionError("no job: TGW_CODING_JOB unset and .tgw-harness/job.json absent or invalid") from exc
    for field in ("task_id", "body", "worktree"):
        if not isinstance(job.get(field), str) or not job[field]:
            raise SessionError(f"job lacks {field}")
    return job


# back-compat alias for tests
_job_from_env = _load_job


def _emit(worktree: Path, name: str, receipt: dict[str, Any]) -> None:
    path = worktree / name
    path.write_text(json.dumps(receipt, sort_keys=True, indent=2), encoding="utf-8")


def implement_main() -> int:
    job = _load_job()
    receipt = run_implement_session(job)
    _emit(Path(job["worktree"]), "implementation-receipt.json", receipt)
    return 0


def review_main() -> int:
    job = _load_job()
    receipt = run_review_session(job)
    _emit(Path(job["worktree"]), "review-receipt.json", receipt)
    return 0


if __name__ == "__main__":  # `python -m tgw.development.harness_session {implement|review}`
    import sys

    _mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if _mode == "implement":
        raise SystemExit(implement_main())
    if _mode == "review":
        raise SystemExit(review_main())
    print("usage: python -m tgw.development.harness_session {implement|review}", file=sys.stderr)
    raise SystemExit(2)
