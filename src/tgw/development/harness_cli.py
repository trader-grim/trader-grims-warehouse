"""CLI entry to run one task through the continual-harness orchestrator —
Todo 1916 leaf 11.1.

Phase-0 bridge: a supervised session runs

    tgw-harness-run <todo-id> --message "<commit subject>"

which builds the production Runners (harness_runners.build_runners) and drives
harness_orchestrator.run_task. The base-branch advance goes through
``sudo -u db git update-ref`` so tgw.main_ref_guard accepts it (the caller is
usually a coder identity, not the sanctioned publisher). When the harness
itself runs as ``db``, pass --self-publish.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from tgw.development import harness_git, harness_ledger, harness_orchestrator, harness_runners

_REPOSITORY = "/opt/TGW/tgw-lib/src/trader-grims-warehouse"
_WORKTREE_ROOT = "/opt/TGW/var/worktrees"
_CODING_CONFIG = Path("/opt/TGW/tgw-lib/config/tgw-coding-local.json")

# The coder session runs under sudo with a minimal secure_path; the orchestrator
# resolves each executor binary here and passes the absolute path on the job.
_EXECUTOR_BIN_CANDIDATES = {
    "claude": ("/usr/local/bin/claude", "/home/claude/.local/bin/claude"),
    "codex": ("/usr/local/bin/codex",),
}


def _discover_executor_bin(name: str, override: str | None) -> str | None:
    if override:
        return override
    found = shutil.which(name)
    if found:
        return found
    return next((c for c in _EXECUTOR_BIN_CANDIDATES.get(name, ()) if Path(c).exists()), None)


def _state_dsn(override: str | None) -> str:
    """DSN for the Todo store and the harness ledger. Both default to the
    tgw-prod ``dbname=state_machine user=tgw`` shape, which is wrong on tgw-lib
    (peer auth: no ``tgw`` role) — resolve the local coding-state DSN instead."""
    if override:
        return override
    env = os.environ.get("TGW_TODO_DSN")
    if env:
        return env
    try:
        dsn = json.loads(_CODING_CONFIG.read_text(encoding="utf-8")).get("postgres_dsn")
    except (OSError, ValueError):
        dsn = None
    if not dsn:
        raise SystemExit(
            f"no state DB DSN: pass --postgres-dsn, set TGW_TODO_DSN, or ensure "
            f"{_CODING_CONFIG} carries postgres_dsn"
        )
    return dsn


def _sudo_ref_publisher(repository: Path, publisher_user: str) -> harness_git.RefPublisher:
    """Advance the base ref as ``publisher_user`` via sudo — for a phase-0
    supervised session that is not itself the sanctioned publisher. When the
    orchestrator runs AS the publisher, pass --self-publish and skip this."""
    def publish(ref: str, old_oid: str, new_oid: str) -> None:
        result = subprocess.run(
            ["sudo", "-n", "-u", publisher_user, "/usr/bin/git", "-C", str(repository),
             "update-ref", ref, new_oid, old_oid],
            check=False, text=True, capture_output=True, timeout=60,
        )
        if result.returncode:
            raise harness_git.HarnessGitError(
                f"ref publish of {ref} as {publisher_user} failed: "
                f"{(result.stderr or result.stdout).strip()[-400:]}"
            )

    return publish


def _task_body(target: str, override: str | None, dsn: str) -> tuple[str, str]:
    """Return (task_id, body). A numeric target is a Todo id; its body is read
    from the store unless --body overrides it."""
    if override:
        return target, override
    if target.isdigit():
        from tgw import todo

        todo.init(dsn)
        item = todo.todo_get(int(target))
        if item is None or not str(item.get("body") or "").strip():
            raise SystemExit(f"Todo {target} has no body; pass --body")
        return f"todo-{target}", str(item["body"])
    raise SystemExit("a non-numeric task id requires --body")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tgw-harness-run")
    parser.add_argument("target", help="Todo id (numeric) or task id (with --body)")
    parser.add_argument("--message", required=True, help="commit subject for the accepted task")
    parser.add_argument("--body", default=None, help="task body (required for a non-numeric target)")
    parser.add_argument("--repository", default=_REPOSITORY)
    parser.add_argument("--worktree-root", default=_WORKTREE_ROOT)
    parser.add_argument("--max-rounds", type=int, default=harness_orchestrator.DEFAULT_MAX_ROUNDS)
    parser.add_argument("--self-publish", action="store_true",
                        help="the harness runs as the sanctioned publisher; advance the ref directly")
    parser.add_argument("--publisher-user", default="tgw-harness",
                        help="sudo target for the ref advance when not --self-publish")
    parser.add_argument("--coder-user", default="tgw-coder",
                        help="run implement/review sessions as this confined user ('' = current user)")
    parser.add_argument("--executor-preference", default="",
                        help="ordered executor list to try, comma-separated (e.g. claude,codex); "
                             "empty = model selector / default chain")
    parser.add_argument("--python", default="/opt/TGW/.venvs/controller/bin/python3")
    parser.add_argument("--claude-bin", default=None,
                        help="absolute path to the claude executable for the coder session "
                             "(default: PATH, then known locations)")
    parser.add_argument("--codex-bin", default=None,
                        help="absolute path to the codex executable for the coder session")
    parser.add_argument("--postgres-dsn", default=None,
                        help="state DB DSN for the Todo store and the harness ledger "
                             "(default: TGW_TODO_DSN or tgw-coding-local.json postgres_dsn)")
    parser.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = parser.parse_args(argv)

    dsn = _state_dsn(args.postgres_dsn)
    harness_ledger.init(dsn)

    repository = Path(args.repository).resolve(strict=True)
    task_id, body = _task_body(args.target, args.body, dsn)

    pref = tuple(e.strip() for e in args.executor_preference.split(",") if e.strip())
    executor_bin = {
        name: path
        for name, path in (
            ("claude", _discover_executor_bin("claude", args.claude_bin)),
            ("codex", _discover_executor_bin("codex", args.codex_bin)),
        )
        if path
    }
    runners = harness_runners.build_runners(
        repository, args.worktree_root, task_body=body, python=args.python,
        coder_user=(args.coder_user or None),
        executor_preference=(pref or None),
        executor_bin=(executor_bin or None),
    )
    ref_publisher = (
        None if args.self_publish
        else _sudo_ref_publisher(repository, args.publisher_user)
    )

    result: dict[str, Any] = harness_orchestrator.run_task(
        task_id,
        repository=repository,
        runners=runners,
        commit_message=args.message,
        max_rounds=args.max_rounds,
        ref_publisher=ref_publisher,
        trailer_lines=(
            "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>",
        ),
    )

    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        outcome = result.get("outcome")
        print(f"{task_id}: {outcome}")
        if outcome == "landed":
            print(f"  commit {result['commit']} on {result.get('base_ref')} after {result['rounds']} round(s)")
        elif outcome == "blocked":
            print(f"  {result['supervisor_handoff']['next_operator_action']}")
        elif outcome in {"already_satisfied", "already_done", "rebind_required"}:
            print(f"  {result}")
    return 0 if result.get("outcome") in {"landed", "already_satisfied", "already_done"} else 1


if __name__ == "__main__":
    sys.exit(main())
