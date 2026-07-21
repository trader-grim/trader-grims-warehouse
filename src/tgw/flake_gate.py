"""
tgw.flake_gate — flake-mutation push/switch request gate (PP-FLAKEGATE-001, todo #1621).

Replaces `nix-flake-maintainer` running `git push` / `nixos-rebuild switch`
directly on ``~/tgw-flake`` with a state-machine-queued request, the same
shape already proven for `enqueue_job()`/`queue_jobs` (PP-STATEMACHINE-001)
and `ebay_publish`'s manual-trigger-only pattern (`tgw publish <sku>`,
`cmd_publish()` in `api.py`).

Live incident this closes: 2026-07-21, `nix-flake-maintainer` committed
AND pushed a flake commit to `origin/master` without Dave's explicit push
confirmation — a compound `git commit && git push` Bash call slipped past
both the permission-prompt UI (Auto Mode) and PreToolUse hooks (confirmed
broken for Agent-tool subagents, anthropics/claude-code#69260). See
invariant E17 (`reference/invariants.md`) and `TGW-Master-Plan.md`'s
PP-FLAKEGATE-001 section for full context.

**This module never shells out to git or nixos-rebuild, anywhere.** It only
ever manages `queue_jobs` rows in the `flake_mutation` queue. No systemd
worker unit polls this queue — nothing but a human calling
`mark_flake_mutation_executed()` (via `tgw flake mark-executed`) ever closes
a job, and only after they've actually run the real command themselves, by
hand, outside this tool.

CLI: ``tgw flake [request-push|request-switch|queue|show|mark-executed|audit]``
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tgw.queue import state_machine


def request_push(repo: str, host: str, commit: str, summary: str) -> Dict[str, Any]:
    """Enqueue a request for a human to `git push` `commit` on `repo` on `host`.

    Does NOT touch git at all — pure Postgres insert, same as cmd_publish()
    does for ebay_publish."""
    dedupe_key = f"flake_mutation:push:{host}:{commit}"
    job_id = state_machine.enqueue_job(
        queue_name=state_machine.FLAKE_MUTATION_QUEUE,
        payload={"repo": repo, "host": host, "kind": "push", "summary": summary},
        entity_type="flake_commit",
        entity_id=commit,
        operation="push",
        dedupe_key=dedupe_key,
        max_attempts=1,
    )
    return {"ok": True, "job_id": job_id, "kind": "push", "host": host, "commit": commit}


def request_switch(host: str, commit: str, summary: str) -> Dict[str, Any]:
    """Enqueue a request for a human to `nixos-rebuild switch` `commit` on `host`.

    Does NOT touch nixos-rebuild at all — pure Postgres insert."""
    dedupe_key = f"flake_mutation:switch:{host}:{commit}"
    job_id = state_machine.enqueue_job(
        queue_name=state_machine.FLAKE_MUTATION_QUEUE,
        payload={"host": host, "kind": "switch", "summary": summary},
        entity_type="flake_commit",
        entity_id=commit,
        operation="switch",
        dedupe_key=dedupe_key,
        max_attempts=1,
    )
    return {"ok": True, "job_id": job_id, "kind": "switch", "host": host, "commit": commit}


def _short_sha(sha: str) -> str:
    return sha[:12] if sha else sha


def queue_table(state: str = "queued") -> Dict[str, Any]:
    """Rows still pending a human decision (default state='queued') — what
    `tgw flake queue` prints for Dave to look at."""
    rows = state_machine.list_flake_mutation_jobs(state=state)
    table: List[Dict[str, Any]] = []
    for r in rows:
        payload = r.get("payload_json") or {}
        table.append({
            "job_id": r["job_id"],
            "kind": payload.get("kind", r.get("operation")),
            "host": payload.get("host"),
            "commit": _short_sha(r.get("entity_id", "")),
            "summary": payload.get("summary", ""),
            "state": r.get("state"),
            "requested_at": r.get("created_at"),
        })
    return {"ok": True, "jobs": table}


def show_job(job_id: str) -> Dict[str, Any]:
    """Full detail of one flake_mutation job."""
    row = state_machine.get_flake_mutation_job(job_id)
    if row is None:
        return {"ok": False, "error": f"no flake_mutation job found for job_id={job_id!r}"}
    return {"ok": True, "job": row}


def mark_executed(job_id: str, executed_by: Optional[str] = None) -> Dict[str, Any]:
    """Record that a human ran the real git push / nixos-rebuild switch
    themselves, by hand. This never executes anything — see module docstring."""
    try:
        row = state_machine.mark_flake_mutation_executed(job_id, executed_by=executed_by)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "job": row}


# Rollout date for this mechanism — commits landing on origin/master before
# this date predate the gate entirely and are not findings even with no
# matching executed flake_mutation record. Matches todo #1621's dispatch
# date; adjust only if the gate's actual go-live date is confirmed later
# than this (deviation flagged, not silently changed).
ROLLOUT_DATE = datetime(2026, 7, 21, tzinfo=timezone.utc)


def audit(repo_path: str) -> Dict[str, Any]:
    """Detective backstop (E17): compare `git log origin/master` commits in
    `repo_path` against `flake_mutation` queue_jobs entries marked executed
    with operation='push'. Any commit on origin/master, dated after
    ROLLOUT_DATE, with no matching executed flake_mutation push record is a
    finding — printed, never silently passed. This is a compensating
    control (preventive PreToolUse hooks are confirmed broken for
    Agent-tool subagents, E11/E12's known gap), not a blocker: it does not
    stop anything, it only reports.
    """
    repo = Path(repo_path).expanduser()
    if not (repo / ".git").exists():
        return {"ok": False, "error": f"not a git repo: {repo}"}

    try:
        subprocess.run(
            ["git", "fetch", "origin"], cwd=repo, check=True,
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        return {"ok": False, "error": f"git fetch origin failed: {exc}"}

    try:
        log = subprocess.run(
            ["git", "log", "origin/master", "--format=%H %cI"],
            cwd=repo, check=True, capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        return {"ok": False, "error": f"git log origin/master failed: {exc}"}

    commits: List[Dict[str, str]] = []
    for line in log.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, iso_date = line.partition(" ")
        commits.append({"sha": sha, "date": iso_date})

    executed_shas = set(state_machine.list_executed_flake_push_shas())

    findings: List[Dict[str, str]] = []
    for c in commits:
        try:
            commit_dt = datetime.fromisoformat(c["date"])
        except ValueError:
            continue
        if commit_dt < ROLLOUT_DATE:
            continue
        if c["sha"] not in executed_shas:
            findings.append(c)

    return {
        "ok": True,
        "repo": str(repo),
        "commits_checked": len(commits),
        "findings": findings,
    }
