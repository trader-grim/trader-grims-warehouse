"""Local Unix-user coding workflow for the Luet-resolved TGW Plan.

The Plan solution selects a leaf.  This module binds that leaf to the ordinary
Todo table and a Git worktree owned through the ``tgw-coders`` group.  Foreman
then schedules the existing local coding treatments in PostgreSQL.  There is
no remote provision service, actor fleet, execution card, or production host.
"""

from __future__ import annotations

import argparse
import dataclasses
import grp
import json
import os
import pwd
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

from tgw import todo
from tgw.development.foreman import ForemanConfig, tick
from tgw.development.plan_todo_bridge import bind_leaf
from tgw.development.treatments import CODEX_IMPLEMENT, CONTROLLER_VERIFY
from tgw.plan_luet import verify_direct_development_solution
from tgw.queue import state_machine
from tgw.workers.coding import CodingWorker
from tgw.workflow import compile_solution_runtime

DEFAULT_CONFIG = Path("/opt/TGW/tgw-lib/config/tgw-coding-local.json")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_REQUEST = re.compile(r"plan-[0-9a-f]{24}\Z")
_LOCAL_TREATMENTS = (CODEX_IMPLEMENT, CONTROLLER_VERIFY)


class LocalCodingWorkflowError(RuntimeError):
    """The direct local workflow cannot safely bind or execute the request."""


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repository, check=False, text=True, capture_output=True,
    )
    if result.returncode:
        raise LocalCodingWorkflowError(f"Git command failed: {result.stderr[-500:]}")
    return result.stdout.strip()


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    """Load the non-secret local coding/database configuration."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalCodingWorkflowError("local coding configuration is unavailable") from exc
    coding = value.get("coding") if isinstance(value, Mapping) else None
    if (
        value.get("schema") != "tgw-local-coding-workflow/v1"
        or not isinstance(value.get("postgres_dsn"), str)
        or not value["postgres_dsn"].strip()
        or not isinstance(coding, Mapping)
    ):
        raise LocalCodingWorkflowError("local coding configuration is invalid")
    repository = Path(str(coding.get("repository_root", "")))
    worktrees = Path(str(coding.get("worktree_root", "")))
    commands = coding.get("commands")
    allowed = coding.get("allowed_runners")
    if (
        not repository.is_absolute()
        or not worktrees.is_absolute()
        or not isinstance(commands, Mapping)
        or not isinstance(allowed, list)
        or not all(isinstance(item, str) and Path(item).is_absolute() for item in allowed)
    ):
        raise LocalCodingWorkflowError("local coding paths or runners are invalid")
    return dict(value)


def require_coder_account(group_name: str = "tgw-coders") -> str:
    """Return the invoking Unix account after proving group membership."""
    actor = pwd.getpwuid(os.geteuid()).pw_name
    group = grp.getgrnam(group_name)
    memberships = set(os.getgroups()) | {os.getegid()}
    if group.gr_gid not in memberships and actor not in group.gr_mem:
        raise LocalCodingWorkflowError(f"Unix account {actor} is not in {group_name}")
    return actor


def load_solution(path: Path | str) -> dict[str, Any]:
    """Load either a solution or its checked runtime-projection wrapper."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalCodingWorkflowError("Luet solution input is unavailable") from exc
    if isinstance(value, Mapping) and value.get("schema") == "tgw-plan-runtime-projection/v1":
        value = value.get("solution")
    if not isinstance(value, Mapping) or value.get("schema") != "tgw-plan-solution/v1":
        raise LocalCodingWorkflowError("Luet solution input is invalid")
    solution = dict(value)
    try:
        verify_direct_development_solution(solution)
    except ValueError as exc:
        raise LocalCodingWorkflowError(str(exc)) from exc
    return solution


def _worktree_identity(repository: Path, worktree: Path) -> tuple[Path, Path, str, str]:
    values = _git(worktree, "rev-parse", "--show-toplevel", "--git-common-dir", "HEAD", "--abbrev-ref", "HEAD").splitlines()
    if len(values) != 4:
        raise LocalCodingWorkflowError("worktree Git identity is incomplete")
    top = Path(values[0]).resolve()
    common = Path(values[1])
    common = (worktree / common).resolve() if not common.is_absolute() else common.resolve()
    expected_common = Path(_git(repository, "rev-parse", "--git-common-dir"))
    expected_common = (
        repository / expected_common
        if not expected_common.is_absolute()
        else expected_common
    ).resolve()
    if top != worktree.resolve() or common != expected_common:
        raise LocalCodingWorkflowError("worktree does not belong to the configured repository")
    return top, common, values[2], values[3]


def allocate_worktree(
    repository: Path,
    worktree_root: Path,
    actor: str,
    todo_id: int,
    request_id: str,
    source_commit: str,
) -> dict[str, Any]:
    """Create or revalidate one ordinary group-owned top-level Git worktree."""
    if todo_id <= 0 or _REQUEST.fullmatch(request_id) is None or _COMMIT.fullmatch(source_commit) is None:
        raise LocalCodingWorkflowError("worktree request identity is invalid")
    if not repository.is_dir() or not worktree_root.is_dir():
        raise LocalCodingWorkflowError("configured repository or worktree root is unavailable")
    if _git(repository, "cat-file", "-t", source_commit) != "commit":
        raise LocalCodingWorkflowError("requested source is not a Git commit")
    name = f"todo-{todo_id}-{request_id}"
    branch = f"coding/{actor}/{name}"
    worktree = worktree_root / name
    created = False
    if not worktree.exists() and not worktree.is_symlink():
        added = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(worktree), source_commit],
            cwd=repository, check=False, text=True, capture_output=True,
        )
        if added.returncode:
            raise LocalCodingWorkflowError(f"failed to create local worktree: {added.stderr[-500:]}")
        created = True
    _top, _common, head, observed_branch = _worktree_identity(repository, worktree)
    if head != source_commit or observed_branch != branch:
        raise LocalCodingWorkflowError("existing worktree differs from the requested identity")
    return {
        "schema": "tgw-local-coding-worktree/v1",
        "repository_root": str(repository.resolve()),
        "worktree": str(worktree.resolve()),
        "todo_id": todo_id,
        "request_id": request_id,
        "branch": branch,
        "head": head,
        "actor": actor,
        "group": "tgw-coders",
        "created": created,
    }


def _execution_root(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.pp_ref:
        return {"schema": "tgw-execution-root/v1", "kind": "pp", "pp_ref": args.pp_ref}
    if args.todo_id:
        return {"schema": "tgw-execution-root/v1", "kind": "todo", "todo_id": args.todo_id}
    return None


def bind_command(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    actor = require_coder_account()
    solution = load_solution(args.solution)
    compiled = compile_solution_runtime(
        solution, current_plan_commit=solution["plan_commit"],
    )
    coding = config["coding"]
    repository = Path(coding["repository_root"]).resolve()
    worktree_root = Path(coding["worktree_root"]).resolve()
    source_commit = args.source_commit or _git(repository, "rev-parse", "HEAD")
    if _COMMIT.fullmatch(source_commit) is None:
        raise LocalCodingWorkflowError("source commit is invalid")
    todo.init(config["postgres_dsn"])
    state_machine.init(config["postgres_dsn"])

    def create_todo(
        agent: str, body: str, priority: int, source: str,
        pp_ref: str | None, anchor: str | None,
    ) -> Mapping[str, Any]:
        return todo.todo_add(
            agent, body, priority, source, pp_ref=pp_ref, plan_anchor=anchor,
            suppress_plan_render=True,
        )

    return bind_leaf(
        compiled,
        solution=solution,
        treatment_id=args.treatment_id,
        source_commit=source_commit,
        worktree_identity=f"unix:{actor}",
        agent=args.agent,
        body=args.body,
        priority=args.priority,
        create_todo=create_todo,
        list_todos=lambda: todo.todo_list(show_all=False),
        allocate_worktree=lambda todo_id, request_id, source: allocate_worktree(
            repository, worktree_root, actor, todo_id, request_id, source,
        ),
        set_status_note=lambda todo_id, note: todo.todo_set_status_note(
            todo_id, note, suppress_plan_render=True,
        ),
        execution_root=_execution_root(args),
    )


def foreman_command(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    require_coder_account()
    todo.init(config["postgres_dsn"])
    state_machine.init(config["postgres_dsn"])
    result = tick(
        ForemanConfig(
            coding_config=dict(config["coding"]),
            treatments=_LOCAL_TREATMENTS,
        ),
        limit=args.limit,
    )
    return dataclasses.asdict(result)


def worker_command(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    require_coder_account()
    worker = CodingWorker(args.queue, config)
    worker._configured_command(args.queue)
    worker.run()


def status_command(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    actor = require_coder_account()
    coding = config["coding"]
    binding = __import__("tgw.plan_luet", fromlist=["load_direct_development_luet_binding"]).load_direct_development_luet_binding()
    return {
        "schema": "tgw-local-coding-status/v1",
        "ok": True,
        "actor": actor,
        "group": "tgw-coders",
        "database": config["postgres_dsn"],
        "repository_root": coding["repository_root"],
        "worktree_root": coding["worktree_root"],
        "treatments": [item.identity for item in _LOCAL_TREATMENTS],
        "luet": {
            "path": str(binding.executable_path),
            "sha256": binding.sha256,
            "version": binding.version,
            "plan_commit": binding.plan_commit,
            "solution_hash": binding.plan_solution_hash,
        },
        "dependencies": {
            "remote_provision_api": False,
            "actor_fleet": False,
            "execution_card": False,
            "tgw_prod": False,
        },
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tgw-coding-local")
    root.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = root.add_subparsers(dest="operation", required=True)

    bind = commands.add_parser("bind", help="bind one eligible Luet leaf to a Todo/worktree")
    bind.add_argument("--solution", type=Path, required=True)
    bind.add_argument("--treatment-id", required=True)
    bind.add_argument("--source-commit")
    bind.add_argument("--agent", default="codex")
    bind.add_argument("--body", required=True)
    bind.add_argument("--priority", type=int, default=50)
    selected = bind.add_mutually_exclusive_group()
    selected.add_argument("--pp-ref")
    selected.add_argument("--todo-id", type=int)

    foreman = commands.add_parser("foreman", help="run one local Foreman tick")
    foreman.add_argument("--limit", type=int)

    worker = commands.add_parser("worker", help="run one local coding queue worker")
    worker.add_argument(
        "--queue", required=True,
        choices=("codex-implement", "controller-verify"),
    )
    commands.add_parser("status", help="show the direct local workflow binding")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.operation == "bind":
            result = bind_command(args)
        elif args.operation == "foreman":
            result = foreman_command(args)
        elif args.operation == "worker":
            worker_command(args)
            return 0
        else:
            result = status_command(args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
