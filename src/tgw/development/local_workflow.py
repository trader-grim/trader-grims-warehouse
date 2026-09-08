"""Local tgw-lib coding config + Unix-account binding.

The tgw-lib development loop is the continual-harness orchestrator
(LEAF-11-1.DELETE-APPARATUS): ``tgw coding start`` -> harness_orchestrator ->
one squashed fast-forward commit on main. This module is what survives of the
old lifecycle workflow — the non-secret config loader and the tgw-coders group
check the operator CLI and Todo CLI still need. No Foreman, no lifecycle store,
no queue workers, no remote provision service.
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

DEFAULT_CONFIG = Path("/opt/TGW/tgw-lib/config/tgw-coding-local.json")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class LocalCodingWorkflowError(RuntimeError):
    """The local coding configuration or Unix binding is invalid."""


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repository.resolve()}", *args],
        cwd=repository, check=False, text=True, capture_output=True,
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
    if not repository.is_absolute() or not worktrees.is_absolute():
        raise LocalCodingWorkflowError("local coding repository or worktree root is invalid")
    # Any lifecycle_* / commands / allowed_runners / *_root keys a not-yet-refreshed
    # config still carries are dead config from the removed apparatus
    # (LEAF-11-1.DELETE-APPARATUS) — ignored, not an error, so `tgw coding` keeps
    # working until the config file is refreshed.
    return dict(value)


def require_coder_account(group_name: str = "tgw-coders") -> str:
    """Return the invoking Unix account after proving group membership."""
    actor = pwd.getpwuid(os.geteuid()).pw_name
    group = grp.getgrnam(group_name)
    memberships = set(os.getgroups()) | {os.getegid()}
    if group.gr_gid not in memberships and actor not in group.gr_mem:
        raise LocalCodingWorkflowError(f"Unix account {actor} is not in {group_name}")
    return actor
