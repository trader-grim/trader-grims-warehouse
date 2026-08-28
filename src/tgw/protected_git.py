"""Deterministic Git reads for the shared local development repository.

The source repository is intentionally writable by ``tgw-coders``. Service
accounts therefore ignore ambient Git configuration, replacement refs, hooks,
and fsmonitor commands when they verify or archive a candidate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

GIT_EXECUTABLE = "/usr/bin/git"


def protected_git_environment() -> Mapping[str, str]:
    """Return the minimal deterministic environment for service-side Git."""

    return {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }


def protected_git_command(repository: Path, *arguments: str) -> list[str]:
    """Build a no-replacement, no-hook command bound to one worktree."""

    resolved = repository.resolve(strict=True)
    return [
        GIT_EXECUTABLE,
        "--no-replace-objects",
        "-c",
        f"safe.directory={resolved}",
        "-c",
        f"core.worktree={resolved}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.attributesFile=/dev/null",
        *arguments,
    ]
