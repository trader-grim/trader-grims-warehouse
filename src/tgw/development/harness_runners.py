"""Production runner factories for the continual-harness orchestrator —
Todo 1916 leaf 11.1.

The orchestrator (``harness_orchestrator.run_task``) is given a ``Runners``
bundle of four callables. This module builds the real ones:

  * ``git_worktree_prepare``  — a ``prepare_worktree(task_id, base_commit)``
    that adds an ordinary group-owned git worktree branched from the base.
  * ``pytest_gate``           — a ``run_tests(task_id, worktree)`` that runs
    the offline test suite (and, optionally, ruff) in the worktree. This is
    the mechanical completion gate: it decides, not a session.
  * ``external_session``      — an implement/review callable that runs one
    configured runner argv in the worktree with a JSON job payload on
    ``TGW_CODING_JOB`` and reads the receipt file the runner writes.

None of the sessions is trusted for correctness. ``run_tests`` and the
fast-forward land are what actually gate (RLM-RESEARCH-TAIL-CONCEPTS #14).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

_GIT = "/usr/bin/git"
_GIT_ENV = {
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


class RunnerError(RuntimeError):
    """A production runner could not execute."""


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        [_GIT, "-C", str(repository), "-c", f"safe.directory={repository}", *args],
        check=False, text=True, capture_output=True, timeout=120,
        env={**_GIT_ENV, "HOME": str(repository)},
    )
    if result.returncode:
        raise RunnerError(f"git {' '.join(args)}: {(result.stderr or result.stdout).strip()[-400:]}")
    return result.stdout.strip()


# --------------------------------------------------------------------------- #
# prepare_worktree
# --------------------------------------------------------------------------- #

def git_worktree_prepare(
    repository: Path | str,
    worktree_root: Path | str,
    *,
    actor: str = "harness",
) -> Callable[[str, str], Path]:
    """Return ``prepare_worktree(task_id, base_commit) -> Path``.

    Creates ``<worktree_root>/<task_id>`` on branch
    ``coding/<actor>/<task_id>`` at ``base_commit``. Idempotent: an existing
    worktree at the same identity is reused; a different one is refused.
    """
    repository = Path(repository).resolve(strict=True)
    worktree_root = Path(worktree_root)

    def prepare_worktree(task_id: str, base_commit: str) -> Path:
        worktree_root.mkdir(parents=True, exist_ok=True)
        name = task_id
        branch = f"coding/{actor}/{name}"
        worktree = (worktree_root / name).resolve()
        if not worktree.exists():
            _git(repository, "worktree", "add", "-b", branch, str(worktree), base_commit)
        head = _git(worktree, "rev-parse", "HEAD")
        observed = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
        if observed != branch:
            raise RunnerError(
                f"worktree {worktree} is on {observed!r}, expected {branch!r}"
            )
        if head != base_commit:
            # The worktree already carries in-progress rounds; that is fine
            # (the orchestrator squashes on land). Only a branch-identity
            # mismatch is a hard error.
            pass
        return worktree

    return prepare_worktree


# --------------------------------------------------------------------------- #
# run_tests — the mechanical gate
# --------------------------------------------------------------------------- #

def _changed_paths(worktree: Path, base_ref: str) -> set[str]:
    """Repo-relative paths this change touches: working-tree diff vs the
    merge-base with ``base_ref``, plus not-yet-committed new files (``git diff``
    never lists untracked).

    The diff is against the merge-base, not ``base_ref``'s current tip: a
    concurrent land on ``base_ref`` while this task is in flight must not make
    files that landing touched — but this worktree did not — look changed here
    (that pulled unrelated, already-red sibling test files into the gate).
    """
    def _lines(*args: str) -> list[str]:
        try:
            return _git(worktree, *args).splitlines()
        except RunnerError:
            return []

    try:
        base = _git(worktree, "merge-base", base_ref, "HEAD")
    except RunnerError:
        base = base_ref

    return (
        set(_lines("diff", "--name-only", base, "--"))
        | set(_lines("ls-files", "--others", "--exclude-standard"))
    )


def _changed_test_selection(worktree: Path, base_ref: str) -> tuple[list[str], str]:
    """The test files this change should be gated on:

      * ``tests/test_*.py`` changed or newly added by this change, plus
      * the sibling ``tests/test_<name>.py`` for every changed
        ``src/**/<name>.py`` module.

    Returns ``(selection, note)``. An empty selection means the change gates on
    no test — the caller decides what that means; the gate never falls back to
    the whole tree, which on this repo carries ~100 unrelated red tests and is
    not a signal.
    """
    touched = _changed_paths(worktree, base_ref)
    selection: set[str] = set()
    for path in touched:
        name = Path(path).name
        if path.startswith("tests/") and name.startswith("test_") and name.endswith(".py"):
            selection.add(path)
    for src in touched:
        if src.startswith("src/") and src.endswith(".py"):
            selection.add(f"tests/test_{Path(src).stem}.py")

    existing = sorted(p for p in selection if (worktree / p).is_file())
    note = (
        f"{len(existing)} test file(s): {', '.join(existing)}" if existing
        else "change gates on no test file (no changed/new test, no sibling test)"
    )
    return existing, note


def pytest_gate(
    *,
    python: str = sys.executable,
    test_paths: tuple[str, ...] = ("tests",),
    changed_tests_only: bool = True,
    empty_selection_passes: bool = True,
    run_ruff: bool = True,
    ruff_paths: tuple[str, ...] = ("src", "tests"),
    timeout_s: int = 1800,
    base_ref: str = "refs/heads/main",
) -> Callable[[str, Path], dict[str, Any]]:
    """Return ``run_tests(task_id, worktree) -> {"passed": bool, "detail": str}``.

    Runs ``pytest -q -p no:cacheprovider`` in the worktree with
    ``PYTHONDONTWRITEBYTECODE=1`` (ad-hoc bytecode breaks release materialize),
    then ``ruff check`` if requested. Fails closed on any non-zero exit.

    ``changed_tests_only`` scopes pytest to the changed / new / sibling test
    files (see ``_changed_test_selection``) — never the whole ``tests`` tree,
    which carries unrelated red tests on this repo. When that selection is
    empty, ``empty_selection_passes`` decides: pass with a note (default), or
    fail so the loop pushes the session to add a test. ``changed_tests_only``
    False runs ``test_paths`` verbatim (dev / explicit use only).
    """

    def run_tests(task_id: str, worktree: Path) -> dict[str, Any]:
        worktree = Path(worktree)
        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(worktree / "src"),
        }
        selection: tuple[str, ...] = test_paths
        note = f"pytest {' '.join(test_paths)}"
        if changed_tests_only:
            picked, note = _changed_test_selection(worktree, base_ref)
            if not picked:
                return {"passed": bool(empty_selection_passes), "detail": note}
            selection = tuple(picked)

        pytest_cmd = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *selection]
        result = subprocess.run(
            pytest_cmd, cwd=worktree, env=env, check=False, text=True,
            capture_output=True, timeout=timeout_s,
        )
        tail = (result.stdout + result.stderr).strip()[-1200:]
        if result.returncode:
            return {"passed": False, "detail": f"{note}\npytest exit {result.returncode}\n{tail}"}

        if run_ruff:
            lint_targets = sorted(
                p for p in _changed_paths(worktree, base_ref)
                if p.endswith(".py") and p.split("/", 1)[0] in ruff_paths
                and (worktree / p).is_file()
            )
            if lint_targets:
                ruff = subprocess.run(
                    [python, "-m", "ruff", "check", "--no-cache", *lint_targets],
                    cwd=worktree, env=env, check=False, text=True, capture_output=True,
                    timeout=300,
                )
                if ruff.returncode:
                    return {
                        "passed": False,
                        "detail": f"ruff exit {ruff.returncode}\n{(ruff.stdout + ruff.stderr).strip()[-800:]}",
                    }

        return {"passed": True, "detail": f"{note} — {tail[-360:]}"}

    return run_tests


# --------------------------------------------------------------------------- #
# external_session — implement / review via a configured runner argv
# --------------------------------------------------------------------------- #

PayloadBuilder = Callable[[str, Path, dict[str, Any]], dict[str, Any]]


def external_session(
    *,
    runner_argv: tuple[str, ...],
    receipt_name: str,
    payload_builder: PayloadBuilder,
    map_receipt: Callable[[dict[str, Any]], dict[str, Any]],
    timeout_s: int = 1800,
    extra_env: dict[str, str] | None = None,
) -> Callable[..., dict[str, Any]]:
    """Return a callable that runs one configured runner in the worktree.

    The callable signature matches whichever ``Runners`` slot it fills:
      implement(task_id, worktree, *, round, prior_findings) -> dict
      review(task_id, worktree) -> dict
    Extra keyword args are collected into the payload-builder ``context``.

    ``payload_builder`` produces the job object. It is passed BOTH as
    ``TGW_CODING_JOB`` in the env AND written to ``<worktree>/.tgw-harness/job.json``
    — the file is the reliable path when the runner is invoked through
    ``sudo -n -u tgw-coder`` (sudo strips the environment; the worktree is
    group-readable). The runner writes ``<worktree>/<receipt_name>`` and
    ``map_receipt`` translates that receipt.
    """
    if not runner_argv:
        raise ValueError("runner_argv must be non-empty")

    def session(task_id: str, worktree: Path, **context: Any) -> dict[str, Any]:
        worktree = Path(worktree)
        payload = payload_builder(task_id, worktree, dict(context))
        job_json = json.dumps(payload, sort_keys=True)
        job_dir = worktree / ".tgw-harness"
        job_dir.mkdir(exist_ok=True)
        (job_dir / "job.json").write_text(job_json, encoding="utf-8")
        env = {
            **os.environ,
            "TGW_CODING_JOB": job_json,
            "TGW_CODING_WORKTREE_SRC": str(worktree / "src"),
            **(extra_env or {}),
        }
        result = subprocess.run(
            list(runner_argv), cwd=worktree, env=env, check=False, text=True,
            capture_output=True, timeout=timeout_s,
        )
        receipt_path = worktree / receipt_name
        if not receipt_path.is_file():
            raise RunnerError(
                f"{runner_argv[0]} for {task_id} wrote no {receipt_name} "
                f"(exit {result.returncode}): {(result.stderr or result.stdout).strip()[-400:]}"
            )
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise RunnerError(f"{receipt_name} for {task_id} is not readable JSON: {exc}") from exc
        if not isinstance(receipt, dict):
            raise RunnerError(f"{receipt_name} for {task_id} is not a JSON object")
        return map_receipt(receipt)

    return session


def implement_outcome(receipt: dict[str, Any]) -> dict[str, Any]:
    """Map an implementation receipt onto {outcome, summary}."""
    raw = str(receipt.get("outcome", "")).lower()
    outcome = {
        "satisfied": "ok", "ok": "ok", "success": "ok",
        "partial": "partial", "resumable_partial": "partial",
        "failed": "failed", "failure": "failed", "blocked": "failed", "conflict": "failed",
    }.get(raw, "failed")
    summary = ""
    for artifact in receipt.get("artifacts", []):
        if isinstance(artifact, dict):
            summary = str(
                artifact.get("detail") or artifact.get("message")
                or artifact.get("summary") or ""
            ).splitlines()[0][:400]
            if summary:
                break
    return {"outcome": outcome, "summary": summary or raw or "no receipt detail"}


_SESSION_MODULE = "tgw.development.harness_session"


def _session_argv(python: str, mode: str, *, coder_user: str | None) -> tuple[str, ...]:
    """The argv for one coder session.

    When ``coder_user`` is set, the session runs as that confined identity via
    ``sudo -n -u`` — the orchestrator identity is the publisher and must not be
    the one editing source in a ``bypassPermissions`` session (leaf 11.1
    identity split: `tgw-harness` orchestrates, `tgw-coder` executes).
    """
    base = (python, "-m", _SESSION_MODULE, mode)
    if coder_user:
        return ("sudo", "-n", "-u", coder_user, *base)
    return base


def build_runners(
    repository: Path | str,
    worktree_root: Path | str,
    *,
    task_body: str,
    python: str = sys.executable,
    actor: str = "harness",
    coder_user: str | None = "tgw-coder",
    executor_preference: tuple[str, ...] | None = None,
    executor_bin: dict[str, str] | None = None,
    stub_canary_text: str | None = None,
    session_timeout_s: int = 1800,
    implement_argv: tuple[str, ...] | None = None,
    review_argv: tuple[str, ...] | None = None,
) -> Any:
    """Assemble a complete ``harness_orchestrator.Runners`` for one task.

    The bundle closes over ``task_body`` — the orchestrator runs exactly one
    task per ``run_task`` call, so one bundle per task is the natural shape.

    ``coder_user`` (default ``tgw-coder``) runs each implement/review session as
    that confined identity. Pass ``None`` to run them as the current user (dev /
    test only). ``executor_preference`` is an ordered list of executors the
    session tries in turn ("claude", "codex", …); empty = let the session's own
    chain (model selector, then default) decide.
    """
    from tgw.development.harness_orchestrator import Runners

    pref = list(executor_preference) if executor_preference else None
    bins = {k: v for k, v in (executor_bin or {}).items() if v} or None

    def _common(payload: dict[str, Any]) -> dict[str, Any]:
        if pref:
            payload["executor_preference"] = pref
        if bins:
            payload["executor_bin"] = bins
        if stub_canary_text:
            payload["stub_canary_text"] = stub_canary_text
        return payload

    def implement_payload(task_id: str, worktree: Path, context: dict[str, Any]) -> dict[str, Any]:
        return _common({
            "task_id": task_id,
            "body": task_body,
            "worktree": str(worktree),
            "round": context.get("round", 1),
            "prior_findings": context.get("prior_findings", []),
        })

    def review_payload(task_id: str, worktree: Path, _context: dict[str, Any]) -> dict[str, Any]:
        return _common({"task_id": task_id, "body": task_body, "worktree": str(worktree)})

    return Runners(
        prepare_worktree=git_worktree_prepare(repository, worktree_root, actor=actor),
        implement=external_session(
            runner_argv=implement_argv or _session_argv(python, "implement", coder_user=coder_user),
            receipt_name="implementation-receipt.json",
            payload_builder=implement_payload,
            map_receipt=implement_outcome,
            timeout_s=session_timeout_s,
        ),
        run_tests=pytest_gate(python=python),
        review=external_session(
            runner_argv=review_argv or _session_argv(python, "review", coder_user=coder_user),
            receipt_name="review-receipt.json",
            payload_builder=review_payload,
            map_receipt=review_findings,
            timeout_s=session_timeout_s,
        ),
    )


def review_findings(receipt: dict[str, Any]) -> dict[str, Any]:
    """Map a review receipt onto {findings: [{message, blocking}]}."""
    verdict = str(receipt.get("verdict", "")).upper()
    findings: list[dict[str, Any]] = []
    for finding in receipt.get("findings", []):
        if isinstance(finding, dict):
            message = str(
                finding.get("message") or finding.get("detail")
                or finding.get("summary") or finding.get("reason") or ""
            ).strip()
            severity = str(finding.get("severity") or finding.get("level") or "").lower()
            blocking = bool(
                finding.get("blocking")
                if "blocking" in finding
                else severity in {"high", "critical", "blocker", "error"} or verdict == "FAIL"
            )
            if message:
                findings.append({"message": message[:600], "blocking": blocking})
        elif isinstance(finding, str) and finding.strip():
            findings.append({"message": finding.strip()[:600], "blocking": verdict == "FAIL"})
    return {"findings": findings, "verdict": verdict or None}
