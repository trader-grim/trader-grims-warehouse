"""LEAF-11-9 W3 — the harness onboarding canary (three tiers, cheapest first).

``tgw-coding-bootstrap --repair harness`` runs this **as** ``tgw-harness`` (the
sanctioned ``refs/heads/main`` publisher) once per tier. Each tier dispatches a
synthetic task through the REAL orchestrator —
``harness_orchestrator.run_task`` → the real ``harness_git`` squash + FF →
``main_ref_guard`` → ``harness_ledger`` — and reports whether it landed.

  offline    – executor ``stub``: no binary, no network, no credential. THE
               gate — ``--repair harness`` fails unless this lands (or is
               already satisfied). The stub writes only ``.tgw-canary`` with a
               fixed host-independent line, so a re-run against a ``main`` that
               already carries it is ``already_satisfied`` (no new commit).
  free-model – the same dispatch through a zero-cost model chosen by the
               standalone model-currency tool (W7). SKIP when no free route is
               reachable or no free-model executor runner is wired.
  live       – a real trivial task through a configured paid executor. SKIP
               unless a paid credential verifies.

``landed``, ``already_satisfied`` and ``skipped`` are success; only ``failed``
(the pipe is broken) exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from tgw.development import harness_git, harness_ledger, harness_orchestrator, harness_runners

SCHEMA = "tgw-harness-canary/v1"
_TIERS = ("offline", "free-model", "live")
# The shared harness/coder worktree root (setgid tgw-coders): the confined
# tgw-coder session must be able to read+write the canary worktree, so a private
# tempdir will not do.
_DEFAULT_WORKTREE_ROOT = "/opt/TGW/var/worktrees"

# A fixed, host-independent line so a second onboarding run against a main that
# already carries this exact .tgw-canary produces no net change.
_OFFLINE_CANARY_TEXT = (
    "harness offline canary — onboarding pipe proof (LEAF-11-9 W3); safe to delete\n"
)
_TRAILERS = ("Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>",)


def _state_dsn(override: str | None) -> str:
    from tgw.development.harness_cli import _state_dsn as _resolve

    return _resolve(override)


def _free_model() -> dict[str, Any] | None:
    """Top-ranked reachable free coding model, or None (no live free route)."""
    try:
        from tgw.model_currency_adapter import reachable_free_models

        models = reachable_free_models()
    except Exception:
        return None
    return models[0] if models else None


def run_tier(
    tier: str,
    *,
    repository: Path,
    coder_user: str | None,
    python: str,
    worktree_root: Path,
    model: str | None = None,
    max_rounds: int = 2,
) -> dict[str, Any]:
    """Dispatch one canary task. Never raises for an expected SKIP; returns a
    result dict with ``result`` in {landed, already_satisfied, skipped, failed}.
    """
    sha = harness_git._rev(repository, "refs/heads/main")[:8]
    task_id = f"harness-canary-{tier}-{sha}-{int(time.time())}"
    base = {"schema": SCHEMA, "tier": tier, "task_id": task_id}

    if tier == "free-model":
        picked = {"provider": model.split("/")[0], "model_id": model} if model else _free_model()
        if not picked:
            return {**base, "result": "skipped", "reason": "no reachable free model route (W7)"}
        # No free-model executor runner is wired in harness_session yet
        # (_WIRED_RUNNERS = {claude, codex}); the offline gate already proves
        # the pipe. Record the reachable model and skip the dispatch.
        return {
            **base,
            "result": "skipped",
            "reason": "free route reachable but no free-model executor runner wired yet",
            "free_model": picked,
        }

    if tier == "live":
        from tgw import coding_executor_catalog

        ready = [
            name
            for name, spec in coding_executor_catalog.executor_specs(enabled_only=True).items()
            if coding_executor_catalog.session_credential(name, spec=spec)
        ]
        if not ready:
            return {**base, "result": "skipped", "reason": "no paid executor credential verifies"}
        executor_preference: tuple[str, ...] = tuple(ready)
        stub_canary_text = None
        task_body = "harness onboarding live canary: trivial no-op change proof"
        message = "harness onboarding canary: live paid executor pipe proof (LEAF-11-9 W3)"
    else:  # offline
        executor_preference = ("stub",)
        stub_canary_text = _OFFLINE_CANARY_TEXT
        task_body = "harness onboarding offline canary: prove the publish pipe with no LLM"
        message = "harness onboarding canary: offline stub pipe proof (LEAF-11-9 W3)"

    worktree_root.mkdir(parents=True, exist_ok=True)
    runners = harness_runners.build_runners(
        repository,
        worktree_root,
        task_body=task_body,
        python=python,
        coder_user=coder_user,
        executor_preference=executor_preference,
        stub_canary_text=stub_canary_text,
    )
    try:
        result = harness_orchestrator.run_task(
            task_id,
            repository=repository,
            runners=runners,
            commit_message=message,
            max_rounds=max_rounds,
            trailer_lines=_TRAILERS,
        )
    except Exception as exc:  # noqa: BLE001 — a broken pipe is a canary failure, reported not raised
        return {**base, "result": "failed", "detail": f"{type(exc).__name__}: {exc}"}

    outcome = result.get("outcome")
    mapped = {
        "landed": "landed",
        "already_satisfied": "already_satisfied",
        "already_done": "already_satisfied",
    }.get(str(outcome), "failed")
    row = {
        **base,
        "result": mapped,
        "outcome": outcome,
        "rounds": result.get("rounds"),
    }
    if result.get("commit"):
        row["commit"] = result["commit"]
    if mapped == "failed":
        row["detail"] = result.get("supervisor_handoff") or result
        # A non-landing canary must not leave an ephemeral worktree/branch behind.
        leftover = Path((result.get("supervisor_handoff") or {}).get("worktree")
                        or (worktree_root / task_id))
        if leftover.exists():
            try:
                harness_git.abandon_task(repository=repository, worktree=leftover)
            except Exception:
                pass
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tgw-harness-canary")
    parser.add_argument("--tier", choices=_TIERS, default="offline")
    parser.add_argument("--repository", default="/opt/TGW/tgw-lib/src/trader-grims-warehouse")
    parser.add_argument("--coder-user", default="tgw-coder",
                        help="run implement/review sessions as this confined user ('' = current user)")
    parser.add_argument("--python", default="/opt/TGW/.venvs/controller/bin/python3")
    parser.add_argument("--worktree-root", default=_DEFAULT_WORKTREE_ROOT,
                        help="parent for the ephemeral canary worktree (must be "
                             "reachable by the confined coder identity)")
    parser.add_argument("--model", default=None, help="force a free-model id for --tier free-model")
    parser.add_argument("--postgres-dsn", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    harness_ledger.init(_state_dsn(args.postgres_dsn))
    repository = Path(args.repository).resolve(strict=True)
    worktree_root = Path(args.worktree_root)

    try:
        row = run_tier(
            args.tier,
            repository=repository,
            coder_user=(args.coder_user or None),
            python=args.python,
            worktree_root=worktree_root,
            model=args.model,
        )
    finally:
        try:
            harness_git._git(repository, "worktree", "prune")
        except Exception:
            pass

    print(json.dumps(row, sort_keys=True, indent=2 if args.json else None))
    return 0 if row["result"] in {"landed", "already_satisfied", "skipped"} else 1


if __name__ == "__main__":
    sys.exit(main())
