#!/opt/TGW/.venvs/controller/bin/python3
"""Run the actor startup check from its exact materialized release."""

from __future__ import annotations

import json
import os
import pwd
import stat
import sys
from pathlib import Path


def _bootstrap_source_root(
    launcher: Path,
    actor: str,
    *,
    binding_root: Path = Path("/etc/tgw/actors"),
    trusted_uid: int = 0,
) -> Path:
    """Resolve candidate imports before the stable launcher can import TGW."""

    local = launcher.resolve(strict=True).parents[1]
    if (local / "src/tgw/actor_startup.py").is_file():
        return local

    binding_path = binding_root / f"{actor}-startup.json"
    observed = binding_path.stat(follow_symlinks=False)
    if (
        binding_path.is_symlink()
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_uid != trusted_uid
        or observed.st_mode & 0o022
        or observed.st_nlink != 1
        or observed.st_size > 64 * 1024
    ):
        raise RuntimeError("actor startup binding is not protected")
    try:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("actor startup binding is invalid") from exc
    if not isinstance(binding, dict):
        raise RuntimeError("actor startup binding is invalid")
    source = Path(str(binding.get("context_source_root", "")))
    if (
        binding.get("schema") != "tgw-actor-startup-binding/v3"
        or binding.get("actor") != actor
        or not source.is_absolute()
        or source == Path("/tmp")
        or Path("/tmp") in source.parents
        or source == Path("/opt/TGW/var/tmp")
        or Path("/opt/TGW/var/tmp") in source.parents
    ):
        raise RuntimeError("actor startup source binding is invalid")
    protected = (
        (source, stat.S_ISDIR),
        (source / "src", stat.S_ISDIR),
        (source / "src/tgw", stat.S_ISDIR),
        (source / "src/tgw/actor_startup.py", stat.S_ISREG),
    )
    for path, expected_type in protected:
        state = path.stat(follow_symlinks=False)
        if (
            path.is_symlink()
            or not expected_type(state.st_mode)
            or state.st_uid != trusted_uid
            or state.st_mode & 0o022
        ):
            raise RuntimeError("actor startup source is not protected")
    return source


def main() -> int:
    actor = pwd.getpwuid(os.geteuid()).pw_name
    source = _bootstrap_source_root(Path(__file__), actor)
    sys.path.insert(0, str(source / "src"))
    from tgw.actor_startup import main as actor_main

    return actor_main()


if __name__ == "__main__":
    raise SystemExit(main())
