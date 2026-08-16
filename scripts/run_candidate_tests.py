#!/usr/bin/env python3
"""Run one candidate-bound test command and emit a tamper-evident receipt."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from tgw.candidate_manifest import create_test_receipt
from tgw.logging import announce_script_run


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--scope", choices=("focused", "full"), required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a test command is required after --")
    repo = args.repo.resolve()
    commit = _git(repo, "rev-parse", f"{args.candidate}^{{commit}}")
    tree = _git(repo, "rev-parse", f"{commit}^{{tree}}")
    announce_script_run(
        "run_candidate_tests.py", "run tests against the exact closed candidate",
        candidate=commit, scope=args.scope,
    )
    # A receipt must describe the closed object, not whichever tracked or
    # untracked files happen to be present in the caller's worktree.  A
    # short-lived detached worktree gives the command precisely the commit
    # whose tree identity is recorded below.
    with tempfile.TemporaryDirectory(prefix="tgw-candidate-test-") as temporary:
        candidate_root = Path(temporary) / "candidate"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "--detach", str(candidate_root), commit],
            check=True, capture_output=True,
        )
        try:
            # Tests must not depend on write access to the operator's live log
            # directory.  The receipt still identifies the exact candidate,
            # while all transient logging remains under its temporary sandbox.
            environment = dict(os.environ)
            environment.setdefault("TGW_LOG_ROOT", str(candidate_root / ".candidate-test-logs"))
            # An editable package in the parent process must never shadow the
            # candidate being receipted.  Keep any explicitly supplied support
            # paths after the detached candidate's own source tree so imports
            # resolve to the bytes identified by ``source_tree`` above.
            candidate_source = str(candidate_root / "src")
            inherited_pythonpath = environment.get("PYTHONPATH", "")
            environment["PYTHONPATH"] = (
                candidate_source
                if not inherited_pythonpath
                else candidate_source + os.pathsep + inherited_pythonpath
            )
            completed = subprocess.run(
                command, cwd=candidate_root, capture_output=True, env=environment,
            )
        finally:
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", str(candidate_root)],
                check=True, capture_output=True,
            )
    receipt = create_test_receipt(
        scope=args.scope, command=command, source_commit=commit, source_tree=tree,
        returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
