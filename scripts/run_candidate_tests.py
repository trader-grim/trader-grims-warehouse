#!/usr/bin/env python3
"""Run one committed candidate test plan and retain its output evidence."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from tgw.candidate_manifest import (
    create_test_output_artifact,
    create_test_receipt,
    load_candidate_test_plan,
)
from tgw.logging import announce_script_run


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--scope", choices=("focused", "full"), required=True)
    parser.add_argument("--output-artifact", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    commit = _git(repo, "rev-parse", f"{args.candidate}^{{commit}}")
    tree = _git(repo, "rev-parse", f"{commit}^{{tree}}")
    output_path = args.output_artifact.resolve()
    if output_path.exists() or not output_path.parent.is_dir():
        parser.error("--output-artifact must name a new file below an existing directory")
    try:
        test_plan = load_candidate_test_plan(repo, source_commit=commit)
        candidate_runner = subprocess.check_output(
            ["git", "show", f"{commit}:{test_plan['runner_path']}"], cwd=repo,
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        parser.error(f"candidate canonical test plan is unavailable: {exc}")
    try:
        runtime_runner = Path(__file__).resolve().read_bytes()
    except OSError as exc:
        parser.error(f"candidate test runner is unavailable: {exc}")
    if runtime_runner != candidate_runner:
        parser.error("invoked test runner does not match the candidate-pinned runner")
    plan_command = test_plan["commands"][args.scope]
    command = [os.sys.executable, *plan_command]
    announce_script_run(
        "run_candidate_tests.py", "run the canonical test plan against the exact closed candidate",
        candidate=commit, scope=args.scope, test_plan=test_plan["sha256"], runner=test_plan["runner_sha256"],
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
    output = create_test_output_artifact(
        scope=args.scope, command=plan_command,
        source_commit=commit, source_tree=tree, stdout=completed.stdout, stderr=completed.stderr,
    )
    output_path.write_text(json.dumps(output, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    receipt = create_test_receipt(
        scope=args.scope, command=plan_command, source_commit=commit, source_tree=tree,
        returncode=completed.returncode, test_plan=test_plan, output_artifact=output,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
