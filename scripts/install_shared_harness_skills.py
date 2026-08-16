#!/usr/bin/env python3
"""Install TGW's canonical skills through native per-harness discovery paths."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tgw.logging import announce_script_run  # noqa: E402

SKILLS = ("tgw-plan", "tgw-review")
DESTINATIONS = {
    "claude": Path(".claude/skills"),
    "codex": Path(".codex/skills"),
    "hermes": Path(".hermes/skills/tgw"),
}


class SkillInstallError(ValueError):
    pass


def _validate_source(source_root: Path) -> Path:
    source_root = source_root.resolve(strict=True)
    for skill in SKILLS:
        skill_file = source_root / skill / "SKILL.md"
        if not skill_file.is_file() or skill_file.is_symlink():
            raise SkillInstallError(f"canonical skill is unavailable: {skill_file}")
    return source_root


def install(
    *, home: Path, harness: str, source_root: Path, replace_stale_link: bool = False
) -> dict[str, object]:
    if harness not in DESTINATIONS:
        raise SkillInstallError(f"unsupported harness: {harness}")
    home = home.resolve(strict=True)
    source_root = _validate_source(source_root)
    destination = home / DESTINATIONS[harness]
    destination.mkdir(parents=True, exist_ok=True)
    installed: list[dict[str, str]] = []
    for skill in SKILLS:
        source = source_root / skill
        target = destination / skill
        if target.is_symlink():
            if target.resolve(strict=False) == source:
                installed.append({"skill": skill, "path": str(target), "status": "current"})
                continue
            if not replace_stale_link:
                raise SkillInstallError(f"stale skill link requires explicit replacement: {target}")
        elif os.path.lexists(target):
            raise SkillInstallError(f"refusing to replace non-link skill installation: {target}")
        temporary = destination / f".{skill}.link-{os.getpid()}"
        if os.path.lexists(temporary):
            temporary.unlink()
        temporary.symlink_to(source, target_is_directory=True)
        os.replace(temporary, target)
        installed.append({"skill": skill, "path": str(target), "status": "installed"})
    return {
        "schema": "tgw-shared-harness-skills-installation/v1",
        "harness": harness,
        "home": str(home),
        "source_root": str(source_root),
        "skills": installed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="install-shared-harness-skills")
    parser.add_argument("--harness", choices=sorted(DESTINATIONS), required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "agent-services" / "skills",
    )
    parser.add_argument("--replace-stale-link", action="store_true")
    args = parser.parse_args()
    announce_script_run(
        "install_shared_harness_skills.py",
        "link canonical TGW Plan and review skills into one harness account",
        harness=args.harness,
        home=str(args.home),
        source_root=str(args.source_root),
        replace_stale_link=args.replace_stale_link,
    )
    try:
        result = install(
            home=args.home,
            harness=args.harness,
            source_root=args.source_root,
            replace_stale_link=args.replace_stale_link,
        )
    except (OSError, SkillInstallError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
